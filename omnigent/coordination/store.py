"""Durable ACID storage for AgentNexus Multi-Agent Coordination Data Layer.

The coordination control plane lives in the project's main SQLAlchemy
database (same Alembic lineage as conversations/policies), so a server or
host restart keeps runs, messages, outbox items, delivery attempts, audit
events, and workspace leases without a side-channel database.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from omnigent.coordination.types import (
    AgentMessage,
    CoordinationArtifact,
    CoordinationEvent,
    CoordinationRun,
    CoordinationTask,
    DeliveryAttempt,
    OutboxItem,
    WorkspaceMergeOperation,
)
from omnigent.db.db_models import (
    SqlAgentMessage,
    SqlCoordinationArtifact,
    SqlCoordinationEvent,
    SqlCoordinationOutbox,
    SqlCoordinationRun,
    SqlCoordinationTask,
    SqlDeliveryAttempt,
    SqlWorkspaceLease,
    SqlWorkspaceMergeOperation,
    current_workspace_id,
)
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker


class StateTransitionConflict(ValueError):
    """A coordination row moved between a caller's read and its CAS claim.

    Raised by :meth:`CoordinationStore.transition_run_status` and
    :meth:`CoordinationStore.transition_task_status` when a concurrent
    writer already owns the transition. Callers treat this as a successful
    idempotent duplicate (the current DB state is authoritative), never as
    a blank second dispatch.
    """


def get_default_coordination_db_path() -> Path:
    """Return the default database path used when no location is injected.

    Kept for backward compatibility; production wiring passes the app's
    ``conversation_store.storage_location`` so coordination tables share the
    configured database instead of hard-coding a home-directory file.
    """
    home = Path.home() / ".omnigent"
    home.mkdir(parents=True, exist_ok=True)
    return home / "chat.db"


def _database_uri(location: str | Path | None) -> str:
    """Convert an old path-or-URI argument to a SQLAlchemy URI."""
    if location is None:
        return f"sqlite:///{get_default_coordination_db_path()}"
    raw = str(location)
    if raw.startswith(
        (
            "sqlite://",
            "postgresql://",
            "postgres://",
            "mysql://",
            "mariadb://",
        )
    ):
        return raw
    if raw == ":memory:":
        return "sqlite:///:memory:"
    return f"sqlite:///{Path(raw).resolve()}"


def _row_to_run(row: SqlCoordinationRun) -> CoordinationRun:
    return CoordinationRun(
        run_id=row.run_id,
        title=row.title,
        root_session_id=row.root_session_id,
        template=row.template,
        status=row.status,
        budget=json.loads(row.budget_json),
        metadata=json.loads(row.metadata_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_task(row: SqlCoordinationTask) -> CoordinationTask:
    return CoordinationTask(
        task_id=row.task_id,
        run_id=row.run_id,
        title=row.title,
        status=row.status,
        assignee_session_id=row.assignee_session_id,
        assignee_role=row.assignee_role,
        dependencies=json.loads(row.dependencies_json),
        acceptance_criteria=json.loads(row.acceptance_json),
        deadline=row.deadline,
        artifacts=json.loads(row.artifacts_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_message(row: SqlAgentMessage) -> AgentMessage:
    return AgentMessage(
        message_id=row.message_id,
        schema_version=row.schema_version,
        root_session_id=row.root_session_id,
        run_id=row.run_id,
        task_id=row.task_id,
        sender_session_id=row.sender_session_id,
        sender_role=row.sender_role,
        recipient_session_id=row.recipient_session_id,
        recipient_role=row.recipient_role,
        kind=row.kind,
        intent=row.intent,
        payload=json.loads(row.payload_json),
        artifacts=json.loads(row.artifacts_json),
        correlation_id=row.correlation_id,
        in_reply_to=row.in_reply_to,
        idempotency_key=row.idempotency_key,
        message_state=row.message_state,
        consumption_state=row.consumption_state,  # type: ignore[arg-type]
        consumption_receipt=(
            json.loads(row.consumption_receipt_json)
            if row.consumption_receipt_json
            else None
        ),
        effect_unknown_reason=row.effect_unknown_reason,
        consumed_at=row.consumed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_outbox(row: SqlCoordinationOutbox) -> OutboxItem:
    return OutboxItem(
        item_id=row.item_id,
        message_id=row.message_id,
        target_session_id=row.target_session_id,
        target_sequence=row.target_sequence,
        status=row.status,
        payload_json=row.payload_json,
        retry_count=row.retry_count,
        next_retry_at=row.next_retry_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_lease(row: SqlWorkspaceLease) -> object:
    from omnigent.workspaces.lease import WorkspaceLease

    return WorkspaceLease(
        lease_id=row.lease_id,
        workspace_path=row.workspace_path,
        holder_session_id=row.holder_session_id,
        mode=row.mode,  # type: ignore[arg-type]
        fencing_token=row.fencing_token,
        acquired_at=row.acquired_at,
        expires_at=row.expires_at,
    )


def _row_to_merge_operation(row: SqlWorkspaceMergeOperation) -> WorkspaceMergeOperation:
    return WorkspaceMergeOperation(
        operation_id=row.operation_id,
        root_session_id=row.root_session_id,
        holder_session_id=row.holder_session_id,
        repo_path=row.repo_path,
        source_branch=row.source_branch,
        target_branch=row.target_branch,
        expected_source_head=row.expected_source_head,
        expected_target_head=row.expected_target_head,
        dirty_hash=row.dirty_hash,
        fencing_token=row.fencing_token,
        status=row.status,  # type: ignore[arg-type]
        preview=json.loads(row.preview_json),
        result=json.loads(row.result_json) if row.result_json else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_artifact(row: SqlCoordinationArtifact) -> CoordinationArtifact:
    return CoordinationArtifact(
        artifact_id=row.artifact_id,
        root_session_id=row.root_session_id,
        run_id=row.run_id,
        task_id=row.task_id,
        producer_session_id=row.producer_session_id,
        kind=row.kind,  # type: ignore[arg-type]
        digest=row.digest,
        uri=row.uri,
        status=row.status,  # type: ignore[arg-type]
        metadata=json.loads(row.metadata_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class CoordinationStore:
    """Thread-safe SQLAlchemy store for coordination domain entities."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_uri = _database_uri(db_path)
        self._engine = get_or_create_engine(self.db_uri)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.coordination_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.coordination_store",
            immediate=True,
        )

    # ── Run Operations ──────────────────────────────────────────

    def create_run(self, run: CoordinationRun) -> CoordinationRun:
        with self._session("create_run") as sess:
            sess.add(
                SqlCoordinationRun(
                    run_id=run.run_id,
                    title=run.title,
                    root_session_id=run.root_session_id,
                    template=run.template,
                    status=run.status,
                    budget_json=json.dumps(run.budget),
                    metadata_json=json.dumps(run.metadata),
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
            )
        return run

    def get_run(self, run_id: str) -> CoordinationRun | None:
        with self._session("get_run") as sess:
            row = sess.get(SqlCoordinationRun, (current_workspace_id(), run_id))
            return _row_to_run(row) if row else None

    def update_run_status(self, run_id: str, status: str) -> None:
        with self._session("update_run_status") as sess:
            sess.execute(
                update(SqlCoordinationRun)
                .where(
                    SqlCoordinationRun.workspace_id == current_workspace_id(),
                    SqlCoordinationRun.run_id == run_id,
                )
                .values(status=status, updated_at=time.time())
            )

    def transition_run_status(
        self,
        run_id: str,
        status: str,
        *,
        from_statuses: list[str],
    ) -> bool:
        """Atomically transition a run if it still has one of *from_statuses*.

        Returns ``True`` when the row moved, ``False`` when it is already in
        *status* (idempotent repeat), and raises
        :class:`StateTransitionConflict` when a concurrent caller has already
        moved it to a different state. Callers use the return value to decide
        whether they own the transition.
        """
        with self._session_immediate("transition_run_status") as sess:
            row = sess.get(SqlCoordinationRun, (current_workspace_id(), run_id))
            if row is None:
                return False
            if row.status == status:
                return False
            if row.status not in from_statuses:
                raise StateTransitionConflict(
                    f"run {run_id} is {row.status}; cannot transition to {status}"
                )
            row.status = status
            row.updated_at = time.time()
            return True

    def update_run_metadata(self, run_id: str, metadata: dict[str, object]) -> None:
        with self._session("update_run_metadata") as sess:
            sess.execute(
                update(SqlCoordinationRun)
                .where(
                    SqlCoordinationRun.workspace_id == current_workspace_id(),
                    SqlCoordinationRun.run_id == run_id,
                )
                .values(metadata_json=json.dumps(metadata), updated_at=time.time())
            )

    def list_runs(self, root_session_id: str | None = None) -> list[CoordinationRun]:
        with self._session("list_runs") as sess:
            stmt = select(SqlCoordinationRun).where(
                SqlCoordinationRun.workspace_id == current_workspace_id()
            )
            if root_session_id:
                stmt = stmt.where(SqlCoordinationRun.root_session_id == root_session_id)
            stmt = stmt.order_by(SqlCoordinationRun.created_at.desc())
            return [_row_to_run(row) for row in sess.scalars(stmt)]

    # ── Task Operations ─────────────────────────────────────────

    def create_task(self, task: CoordinationTask) -> CoordinationTask:
        with self._session("create_task") as sess:
            sess.add(
                SqlCoordinationTask(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    title=task.title,
                    status=task.status,
                    assignee_session_id=task.assignee_session_id,
                    assignee_role=task.assignee_role,
                    dependencies_json=json.dumps(task.dependencies),
                    acceptance_json=json.dumps(task.acceptance_criteria),
                    deadline=task.deadline,
                    artifacts_json=json.dumps(task.artifacts),
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
            )
        return task

    def get_task(self, task_id: str) -> CoordinationTask | None:
        with self._session("get_task") as sess:
            row = sess.get(SqlCoordinationTask, (current_workspace_id(), task_id))
            return _row_to_task(row) if row else None

    def update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        artifacts: list[dict[str, object]] | None = None,
    ) -> None:
        with self._session("update_task_status") as sess:
            values: dict[str, object] = {"status": status, "updated_at": time.time()}
            if artifacts is not None:
                values["artifacts_json"] = json.dumps(artifacts)
            sess.execute(
                update(SqlCoordinationTask)
                .where(
                    SqlCoordinationTask.workspace_id == current_workspace_id(),
                    SqlCoordinationTask.task_id == task_id,
                )
                .values(**values)
            )

    def transition_task_status(
        self,
        task_id: str,
        status: str,
        *,
        from_statuses: list[str],
        artifacts: list[dict[str, object]] | None = None,
    ) -> bool:
        """Atomically transition a task if it still has one of *from_statuses*.

        Same CAS contract as :meth:`transition_run_status`. This is the
        concurrency boundary that prevents duplicate stage advancement,
        cancellation races, and double dispatch after a retry. Raises
        :class:`StateTransitionConflict` when the state has moved on.
        """
        with self._session_immediate("transition_task_status") as sess:
            row = sess.get(SqlCoordinationTask, (current_workspace_id(), task_id))
            if row is None:
                return False
            if row.status == status:
                return False
            if row.status not in from_statuses:
                raise StateTransitionConflict(
                    f"task {task_id} is {row.status}; cannot transition to {status}"
                )
            row.status = status
            if artifacts is not None:
                row.artifacts_json = json.dumps(artifacts)
            row.updated_at = time.time()
            return True

    def reassign_task(self, task_id: str, assignee_session_id: str) -> bool:
        """Redirect a task to another assignee session (idempotent)."""
        with self._session_immediate("reassign_task") as sess:
            row = sess.get(SqlCoordinationTask, (current_workspace_id(), task_id))
            if row is None:
                return False
            if row.assignee_session_id == assignee_session_id:
                return False
            row.assignee_session_id = assignee_session_id
            row.updated_at = time.time()
            return True

    def list_tasks(self, run_id: str) -> list[CoordinationTask]:
        with self._session("list_tasks") as sess:
            stmt = (
                select(SqlCoordinationTask)
                .where(
                    SqlCoordinationTask.workspace_id == current_workspace_id(),
                    SqlCoordinationTask.run_id == run_id,
                )
                .order_by(SqlCoordinationTask.created_at.asc())
            )
            return [_row_to_task(row) for row in sess.scalars(stmt)]

    # ── Message & Outbox Operations (Transactional) ─────────────

    def save_message_and_outbox(self, message: AgentMessage) -> tuple[AgentMessage, OutboxItem]:
        """Atomically persist an AgentMessage and queue its Outbox delivery item."""
        target_sequence = self._next_target_sequence(
            message.root_session_id, message.recipient_session_id
        )
        outbox = OutboxItem(
            message_id=message.message_id,
            target_session_id=message.recipient_session_id,
            target_sequence=target_sequence,
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
        try:
            with self._session_immediate("save_message_and_outbox") as sess:
                sess.add(
                    SqlAgentMessage(
                        message_id=message.message_id,
                        schema_version=message.schema_version,
                        root_session_id=message.root_session_id,
                        run_id=message.run_id,
                        task_id=message.task_id,
                        sender_session_id=message.sender_session_id,
                        sender_role=message.sender_role,
                        recipient_session_id=message.recipient_session_id,
                        recipient_role=message.recipient_role,
                        kind=message.kind,
                        intent=message.intent,
                        payload_json=json.dumps(message.payload),
                        artifacts_json=json.dumps(message.artifacts),
                        correlation_id=message.correlation_id,
                        in_reply_to=message.in_reply_to,
                        idempotency_key=message.idempotency_key,
                        message_state=message.message_state,
                        consumption_state=message.consumption_state,
                        consumption_receipt_json=(
                            json.dumps(message.consumption_receipt)
                            if message.consumption_receipt is not None
                            else None
                        ),
                        consumed_at=message.consumed_at,
                        created_at=message.created_at,
                        updated_at=message.updated_at,
                    )
                )
                sess.add(
                    SqlCoordinationOutbox(
                        item_id=outbox.item_id,
                        message_id=outbox.message_id,
                        target_session_id=outbox.target_session_id,
                        target_sequence=outbox.target_sequence,
                        status=outbox.status,
                        payload_json=outbox.payload_json,
                        retry_count=0,
                        next_retry_at=outbox.next_retry_at,
                        created_at=outbox.created_at,
                        updated_at=outbox.updated_at,
                    )
                )
                sess.add(
                    SqlCoordinationEvent(
                        event_id=event.event_id,
                        root_session_id=event.root_session_id,
                        run_id=event.run_id,
                        task_id=event.task_id,
                        actor_session_id=event.actor_session_id,
                        event_type=event.event_type,
                        payload_json=json.dumps(event.payload),
                        created_at=event.created_at,
                    )
                )
            return message, outbox
        except IntegrityError:
            if message.idempotency_key is None:
                raise
            existing = self._get_message_by_idempotency(message.idempotency_key)
            if existing is None:
                raise
            existing_outbox = self._outbox_for_message(existing.message_id)
            if existing_outbox is None:
                raise
            return existing, existing_outbox

    def _next_target_sequence(self, root_session_id: str, target_session_id: str) -> int:
        """Return the next monotonic sequence for (root, target) delivery ordering."""
        with self._session("next_target_sequence") as sess:
            stmt = (
                select(func.max(SqlCoordinationOutbox.target_sequence))
                .join(
                    SqlAgentMessage,
                    SqlAgentMessage.message_id == SqlCoordinationOutbox.message_id,
                )
                .where(
                    SqlCoordinationOutbox.workspace_id == current_workspace_id(),
                    SqlAgentMessage.root_session_id == root_session_id,
                    SqlCoordinationOutbox.target_session_id == target_session_id,
                )
            )
            current = sess.scalar(stmt)
            return int(current or 0) + 1

    def _get_message_by_idempotency(self, idempotency_key: str) -> AgentMessage | None:
        with self._session("get_message_by_idempotency") as sess:
            stmt = (
                select(SqlAgentMessage)
                .where(
                    SqlAgentMessage.workspace_id == current_workspace_id(),
                    SqlAgentMessage.idempotency_key == idempotency_key,
                )
                .order_by(SqlAgentMessage.created_at.asc())
                .limit(1)
            )
            row = sess.scalar(stmt)
            return _row_to_message(row) if row else None

    def _outbox_for_message(self, message_id: str) -> OutboxItem | None:
        with self._session("outbox_for_message") as sess:
            stmt = (
                select(SqlCoordinationOutbox)
                .where(
                    SqlCoordinationOutbox.workspace_id == current_workspace_id(),
                    SqlCoordinationOutbox.message_id == message_id,
                )
                .order_by(SqlCoordinationOutbox.created_at.asc())
                .limit(1)
            )
            row = sess.scalar(stmt)
            return _row_to_outbox(row) if row else None

    def get_message(self, message_id: str) -> AgentMessage | None:
        with self._session("get_message") as sess:
            row = sess.get(SqlAgentMessage, (current_workspace_id(), message_id))
            return _row_to_message(row) if row else None

    def update_message_state(self, message_id: str, state: str) -> None:
        with self._session("update_message_state") as sess:
            sess.execute(
                update(SqlAgentMessage)
                .where(
                    SqlAgentMessage.workspace_id == current_workspace_id(),
                    SqlAgentMessage.message_id == message_id,
                )
                .values(message_state=state, updated_at=time.time())
            )

    def cancel_message(self, message_id: str) -> AgentMessage | None:
        """Cancel a queued message; injected/active messages cannot be cancelled."""
        with self._session_immediate("cancel_message") as sess:
            row = sess.get(SqlAgentMessage, (current_workspace_id(), message_id))
            if row is None:
                return None
            if row.message_state in ("cancelled", "expired"):
                return _row_to_message(row)
            if row.message_state != "queued":
                return _row_to_message(row)
            now = time.time()
            row.message_state = "cancelled"
            row.updated_at = now
            sess.execute(
                update(SqlCoordinationOutbox)
                .where(
                    SqlCoordinationOutbox.workspace_id == current_workspace_id(),
                    SqlCoordinationOutbox.message_id == message_id,
                    SqlCoordinationOutbox.status.in_(("pending", "leased")),
                )
                .values(status="failed", updated_at=now)
            )
        return self.get_message(message_id)

    def redirect_message_recipient(
        self, message_id: str, recipient_session_id: str
    ) -> bool:
        """Redirect a still-queued message and its outbox to a new recipient."""
        with self._session_immediate("redirect_message_recipient") as sess:
            row = sess.get(SqlAgentMessage, (current_workspace_id(), message_id))
            if row is None:
                return False
            row.recipient_session_id = recipient_session_id
            row.updated_at = time.time()
            now = time.time()
            sess.execute(
                update(SqlCoordinationOutbox)
                .where(
                    SqlCoordinationOutbox.workspace_id == current_workspace_id(),
                    SqlCoordinationOutbox.message_id == message_id,
                    SqlCoordinationOutbox.status.in_(("pending", "leased")),
                )
                .values(target_session_id=recipient_session_id, updated_at=now)
            )
            return True

    def record_consumption_receipt(
        self,
        message_id: str,
        state: str,
        receipt: dict[str, object] | None = None,
    ) -> AgentMessage | None:
        """Record an explicit target-agent consumption/ack/reject receipt."""
        with self._session_immediate("record_consumption_receipt") as sess:
            row = sess.get(SqlAgentMessage, (current_workspace_id(), message_id))
            if row is None:
                return None
            now = time.time()
            row.consumption_state = state
            row.consumption_receipt_json = json.dumps(receipt) if receipt else None
            row.consumed_at = now if state in ("consumed", "acknowledged", "rejected") else None
            row.updated_at = now
        return self.get_message(message_id)

    def mark_message_effect_unknown(
        self, message_id: str, reason: str
    ) -> AgentMessage | None:
        """Mark an active message's delivery effect as unknown, once.

        The CAS (``reason IS NULL``) makes repeated reconciliation scans
        idempotent: only the first writer stamps the reason, and a later
        consumption receipt does not see a wrongly mutated row here (that
        state transition is owned by
        :meth:`record_consumption_receipt`).
        """
        with self._session_immediate("mark_message_effect_unknown") as sess:
            row = sess.get(SqlAgentMessage, (current_workspace_id(), message_id))
            if row is None or row.effect_unknown_reason is not None:
                return None
            row.effect_unknown_reason = reason
            row.updated_at = time.time()
        return self.get_message(message_id)

    def list_effect_unknown_candidates(self, limit: int = 200) -> list[AgentMessage]:
        """Return active, unconsumed messages that no reconciler has stamped."""
        with self._session("list_effect_unknown_candidates") as sess:
            stmt = (
                select(SqlAgentMessage)
                .where(
                    SqlAgentMessage.workspace_id == current_workspace_id(),
                    SqlAgentMessage.message_state == "active",
                    SqlAgentMessage.consumption_state == "unconsumed",
                    SqlAgentMessage.effect_unknown_reason.is_(None),
                )
                .order_by(SqlAgentMessage.created_at.asc())
                .limit(limit)
            )
            return [_row_to_message(row) for row in sess.scalars(stmt)]

    def list_messages(
        self, root_session_id: str, recipient_session_id: str | None = None
    ) -> list[AgentMessage]:
        with self._session("list_messages") as sess:
            stmt = select(SqlAgentMessage).where(
                SqlAgentMessage.workspace_id == current_workspace_id(),
                SqlAgentMessage.root_session_id == root_session_id,
            )
            if recipient_session_id:
                stmt = stmt.where(
                    SqlAgentMessage.recipient_session_id == recipient_session_id
                )
            stmt = stmt.order_by(SqlAgentMessage.created_at.asc())
            return [_row_to_message(row) for row in sess.scalars(stmt)]

    def list_active_unconsumed_for_recipient(
        self, recipient_session_id: str
    ) -> list[AgentMessage]:
        """Return delivered-but-unconsumed A2A messages addressed to a session.

        Terminal turn completion uses this as the control-plane receipt
        cursor: each active message (already injected, not yet cancelled or
        expired) that still has no consumption receipt is one the recipient
        is expected to acknowledge when its next real turn ends.
        """
        with self._session("list_active_unconsumed_for_recipient") as sess:
            stmt = (
                select(SqlAgentMessage)
                .where(
                    SqlAgentMessage.workspace_id == current_workspace_id(),
                    SqlAgentMessage.recipient_session_id == recipient_session_id,
                    SqlAgentMessage.message_state == "active",
                    SqlAgentMessage.consumption_state == "unconsumed",
                )
                .order_by(SqlAgentMessage.created_at.asc())
            )
            return [_row_to_message(row) for row in sess.scalars(stmt)]

    # ── Outbox Delivery Operations ──────────────────────────────

    def fetch_pending_outbox(self, limit: int = 20) -> list[OutboxItem]:
        with self._session("fetch_pending_outbox") as sess:
            stmt = (
                select(SqlCoordinationOutbox)
                .where(
                    SqlCoordinationOutbox.workspace_id == current_workspace_id(),
                    SqlCoordinationOutbox.status.in_(("pending", "leased")),
                    SqlCoordinationOutbox.next_retry_at <= time.time(),
                )
                .order_by(SqlCoordinationOutbox.next_retry_at.asc())
                .limit(limit)
            )
            return [_row_to_outbox(row) for row in sess.scalars(stmt)]

    def get_outbox_item(self, item_id: str) -> OutboxItem | None:
        with self._session("get_outbox_item") as sess:
            row = sess.get(SqlCoordinationOutbox, (current_workspace_id(), item_id))
            return _row_to_outbox(row) if row else None

    def list_outbox_items(
        self, *, message_id: str | None = None, status: str | None = None
    ) -> list[OutboxItem]:
        with self._session("list_outbox_items") as sess:
            stmt = select(SqlCoordinationOutbox).where(
                SqlCoordinationOutbox.workspace_id == current_workspace_id()
            )
            if message_id:
                stmt = stmt.where(SqlCoordinationOutbox.message_id == message_id)
            if status:
                stmt = stmt.where(SqlCoordinationOutbox.status == status)
            stmt = stmt.order_by(SqlCoordinationOutbox.created_at.asc())
            return [_row_to_outbox(row) for row in sess.scalars(stmt)]

    def claim_pending_outbox(self, limit: int = 20) -> list[OutboxItem]:
        """Atomically claim the next batch of pending outbox items.

        A claim moves rows to ``leased`` so a restart or concurrent dispatching
        cannot replay the same item while it is being delivered. Failed items
        are requeued by :meth:`requeue_outbox`.
        """
        now = time.time()
        claims: list[OutboxItem] = []
        with self._session_immediate("claim_pending_outbox") as sess:
            stmt = (
                select(SqlCoordinationOutbox)
                .where(
                    SqlCoordinationOutbox.workspace_id == current_workspace_id(),
                    SqlCoordinationOutbox.status == "pending",
                    SqlCoordinationOutbox.next_retry_at <= now,
                )
                .order_by(SqlCoordinationOutbox.next_retry_at.asc())
                .limit(limit)
                .with_for_update()
            )
            rows = list(sess.scalars(stmt))
            for row in rows:
                row.status = "leased"
                row.updated_at = time.time()
                claims.append(_row_to_outbox(row))
        return claims

    def record_delivery_attempt(
        self,
        attempt: DeliveryAttempt,
        mark_outbox_done: bool = False,
        outbox_item_id: str | None = None,
    ) -> None:
        with self._session_immediate("record_delivery_attempt") as sess:
            sess.add(
                SqlDeliveryAttempt(
                    attempt_id=attempt.attempt_id,
                    message_id=attempt.message_id,
                    target_session_id=attempt.target_session_id,
                    target_sequence=attempt.target_sequence,
                    target_harness=attempt.target_harness,
                    delivery_mode=attempt.delivery_mode,
                    delivery_state=attempt.delivery_state,
                    injection_receipt_json=(
                        json.dumps(attempt.injection_receipt)
                        if attempt.injection_receipt
                        else None
                    ),
                    error=attempt.error,
                    attempt_count=attempt.attempt_count,
                    created_at=attempt.created_at,
                    updated_at=attempt.updated_at,
                )
            )
            if mark_outbox_done:
                stmt = (
                    update(SqlCoordinationOutbox)
                    .where(
                        SqlCoordinationOutbox.workspace_id == current_workspace_id(),
                        SqlCoordinationOutbox.status.in_(("pending", "leased")),
                    )
                    .values(status=attempt.delivery_state, updated_at=attempt.updated_at)
                )
                if outbox_item_id:
                    stmt = stmt.where(SqlCoordinationOutbox.item_id == outbox_item_id)
                else:
                    stmt = stmt.where(SqlCoordinationOutbox.message_id == attempt.message_id)
                sess.execute(stmt)

    def requeue_outbox(
        self, item_id: str, *, max_retries: int = 5, next_retry_delay_s: float | None = None
    ) -> None:
        """Return a failed/leased item to pending with exponential backoff."""
        with self._session_immediate("requeue_outbox") as sess:
            row = sess.get(SqlCoordinationOutbox, (current_workspace_id(), item_id))
            if row is None:
                return
            retry_count = row.retry_count + 1
            if retry_count >= max_retries:
                row.status = "failed"
                row.retry_count = retry_count
                row.updated_at = time.time()
                return
            delay = (
                next_retry_delay_s
                if next_retry_delay_s is not None
                else min(60.0, 2**retry_count)
            )
            row.status = "pending"
            row.retry_count = retry_count
            row.next_retry_at = time.time() + delay
            row.updated_at = time.time()

    def list_delivery_attempts(self, message_id: str) -> list[DeliveryAttempt]:
        with self._session("list_delivery_attempts") as sess:
            stmt = (
                select(SqlDeliveryAttempt)
                .where(
                    SqlDeliveryAttempt.workspace_id == current_workspace_id(),
                    SqlDeliveryAttempt.message_id == message_id,
                )
                .order_by(SqlDeliveryAttempt.created_at.asc())
            )
            result: list[DeliveryAttempt] = []
            for row in sess.scalars(stmt):
                result.append(
                    DeliveryAttempt(
                        attempt_id=row.attempt_id,
                        message_id=row.message_id,
                        target_session_id=row.target_session_id,
                        target_sequence=row.target_sequence,
                        target_harness=row.target_harness,
                        delivery_mode=row.delivery_mode,  # type: ignore[arg-type]
                        delivery_state=row.delivery_state,  # type: ignore[arg-type]
                        injection_receipt=(
                            json.loads(row.injection_receipt_json)
                            if row.injection_receipt_json
                            else None
                        ),
                        error=row.error,
                        attempt_count=row.attempt_count,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                    )
                )
            return result

    # ── Events Timeline Operations ──────────────────────────────

    def record_event(self, event: CoordinationEvent) -> CoordinationEvent:
        with self._session("record_event") as sess:
            sess.add(
                SqlCoordinationEvent(
                    event_id=event.event_id,
                    root_session_id=event.root_session_id,
                    run_id=event.run_id,
                    task_id=event.task_id,
                    actor_session_id=event.actor_session_id,
                    event_type=event.event_type,
                    payload_json=json.dumps(event.payload),
                    created_at=event.created_at,
                )
            )
        return event

    def list_events(
        self, root_session_id: str, since: float | None = None
    ) -> list[CoordinationEvent]:
        with self._session("list_events") as sess:
            stmt = select(SqlCoordinationEvent).where(
                SqlCoordinationEvent.workspace_id == current_workspace_id(),
                SqlCoordinationEvent.root_session_id == root_session_id,
            )
            if since is not None:
                stmt = stmt.where(SqlCoordinationEvent.created_at > since)
            stmt = stmt.order_by(SqlCoordinationEvent.created_at.asc())
            return [
                CoordinationEvent(
                    event_id=row.event_id,
                    root_session_id=row.root_session_id,
                    run_id=row.run_id,
                    task_id=row.task_id,
                    actor_session_id=row.actor_session_id,
                    event_type=row.event_type,
                    payload=json.loads(row.payload_json),
                    created_at=row.created_at,
                )
                for row in sess.scalars(stmt)
            ]

    # ── Workspace Lease Persistence ─────────────────────────────

    def save_lease(self, lease: object) -> None:
        """Persist a workspace lease row (replacing any active row for path)."""
        from omnigent.workspaces.lease import WorkspaceLease

        if not isinstance(lease, WorkspaceLease):
            raise TypeError("lease must be WorkspaceLease")
        with self._session_immediate("save_lease") as sess:
            sess.execute(
                delete(SqlWorkspaceLease).where(
                    SqlWorkspaceLease.workspace_id == current_workspace_id(),
                    SqlWorkspaceLease.workspace_path == lease.workspace_path,
                    SqlWorkspaceLease.status == "active",
                )
            )
            sess.add(
                SqlWorkspaceLease(
                    lease_id=lease.lease_id,
                    workspace_path=lease.workspace_path,
                    holder_session_id=lease.holder_session_id,
                    mode=lease.mode,
                    fencing_token=lease.fencing_token,
                    status="active",
                    acquired_at=lease.acquired_at,
                    expires_at=lease.expires_at,
                    updated_at=lease.expires_at,
                )
            )

    def load_active_lease(self, workspace_path: str) -> object | None:
        """Load the active lease for a path, ignoring expired rows."""
        with self._session("load_active_lease") as sess:
            stmt = (
                select(SqlWorkspaceLease)
                .where(
                    SqlWorkspaceLease.workspace_id == current_workspace_id(),
                    SqlWorkspaceLease.workspace_path == workspace_path,
                    SqlWorkspaceLease.status == "active",
                    SqlWorkspaceLease.expires_at > time.time(),
                )
                .order_by(SqlWorkspaceLease.acquired_at.desc())
                .limit(1)
            )
            row = sess.scalar(stmt)
            return _row_to_lease(row) if row else None

    def expire_lease(self, workspace_path: str, holder_session_id: str) -> None:
        """Mark the holder's active lease row released/expired."""
        with self._session_immediate("expire_lease") as sess:
            sess.execute(
                update(SqlWorkspaceLease)
                .where(
                    SqlWorkspaceLease.workspace_id == current_workspace_id(),
                    SqlWorkspaceLease.workspace_path == workspace_path,
                    SqlWorkspaceLease.holder_session_id == holder_session_id,
                    SqlWorkspaceLease.status == "active",
                )
                .values(status="released", updated_at=time.time())
            )

    # ── Workspace Merge Operation Persistence ──────────────────

    def create_merge_operation(
        self, operation: WorkspaceMergeOperation
    ) -> WorkspaceMergeOperation:
        """Persist a new user-confirmed merge operation."""
        with self._session("create_merge_operation") as sess:
            sess.add(
                SqlWorkspaceMergeOperation(
                    operation_id=operation.operation_id,
                    root_session_id=operation.root_session_id,
                    holder_session_id=operation.holder_session_id,
                    repo_path=operation.repo_path,
                    source_branch=operation.source_branch,
                    target_branch=operation.target_branch,
                    expected_source_head=operation.expected_source_head,
                    expected_target_head=operation.expected_target_head,
                    dirty_hash=operation.dirty_hash,
                    fencing_token=operation.fencing_token,
                    status=operation.status,
                    preview_json=json.dumps(operation.preview),
                    result_json=(
                        json.dumps(operation.result) if operation.result is not None else None
                    ),
                    created_at=operation.created_at,
                    updated_at=operation.updated_at,
                )
            )
        return operation

    def get_merge_operation(self, operation_id: str) -> WorkspaceMergeOperation | None:
        """Load one merge operation by id."""
        with self._session("get_merge_operation") as sess:
            row = sess.get(SqlWorkspaceMergeOperation, (current_workspace_id(), operation_id))
            return _row_to_merge_operation(row) if row else None

    def list_merge_operations(
        self, root_session_id: str | None = None
    ) -> list[WorkspaceMergeOperation]:
        """List merge operations, optionally scoped to a coordination root."""
        with self._session("list_merge_operations") as sess:
            stmt = select(SqlWorkspaceMergeOperation).where(
                SqlWorkspaceMergeOperation.workspace_id == current_workspace_id()
            )
            if root_session_id:
                stmt = stmt.where(
                    SqlWorkspaceMergeOperation.root_session_id == root_session_id
                )
            stmt = stmt.order_by(SqlWorkspaceMergeOperation.created_at.desc())
            return [_row_to_merge_operation(row) for row in sess.scalars(stmt)]

    def update_merge_operation(
        self,
        operation_id: str,
        *,
        status: str | None = None,
        result: dict[str, object] | None = None,
    ) -> WorkspaceMergeOperation | None:
        """Update merge operation status/result and return the fresh row."""
        values: dict[str, object] = {"updated_at": time.time()}
        if status is not None:
            values["status"] = status
        if result is not None:
            values["result_json"] = json.dumps(result)
        with self._session_immediate("update_merge_operation") as sess:
            sess.execute(
                update(SqlWorkspaceMergeOperation)
                .where(
                    SqlWorkspaceMergeOperation.workspace_id == current_workspace_id(),
                    SqlWorkspaceMergeOperation.operation_id == operation_id,
                )
                .values(**values)
            )
        return self.get_merge_operation(operation_id)

    # ── Artifact Metadata Operations ───────────────────────────

    def create_artifact(self, artifact: CoordinationArtifact) -> CoordinationArtifact:
        """Persist one control-plane artifact metadata row."""
        with self._session("create_artifact") as sess:
            sess.add(
                SqlCoordinationArtifact(
                    artifact_id=artifact.artifact_id,
                    root_session_id=artifact.root_session_id,
                    run_id=artifact.run_id,
                    task_id=artifact.task_id,
                    producer_session_id=artifact.producer_session_id,
                    kind=artifact.kind,
                    digest=artifact.digest,
                    uri=artifact.uri,
                    status=artifact.status,
                    metadata_json=json.dumps(artifact.metadata),
                    created_at=artifact.created_at,
                    updated_at=artifact.updated_at,
                )
            )
        return artifact

    def get_artifact(self, artifact_id: str) -> CoordinationArtifact | None:
        """Load one artifact metadata row by id."""
        with self._session("get_artifact") as sess:
            row = sess.get(SqlCoordinationArtifact, (current_workspace_id(), artifact_id))
            return _row_to_artifact(row) if row else None

    def list_artifacts(
        self,
        root_session_id: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[CoordinationArtifact]:
        """List artifact metadata for a root, optionally narrowed by run/task."""
        with self._session("list_artifacts") as sess:
            stmt = select(SqlCoordinationArtifact).where(
                SqlCoordinationArtifact.workspace_id == current_workspace_id(),
                SqlCoordinationArtifact.root_session_id == root_session_id,
            )
            if run_id:
                stmt = stmt.where(SqlCoordinationArtifact.run_id == run_id)
            if task_id:
                stmt = stmt.where(SqlCoordinationArtifact.task_id == task_id)
            stmt = stmt.order_by(SqlCoordinationArtifact.created_at.asc())
            return [_row_to_artifact(row) for row in sess.scalars(stmt)]

    def update_artifact_status(
        self, artifact_id: str, status: str
    ) -> CoordinationArtifact | None:
        """Mark an artifact published/updated/invalidated and return the fresh row."""
        with self._session_immediate("update_artifact_status") as sess:
            sess.execute(
                update(SqlCoordinationArtifact)
                .where(
                    SqlCoordinationArtifact.workspace_id == current_workspace_id(),
                    SqlCoordinationArtifact.artifact_id == artifact_id,
                )
                .values(status=status, updated_at=time.time())
            )
        return self.get_artifact(artifact_id)
