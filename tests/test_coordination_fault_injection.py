"""Fault injection, G1, G2, G4, and G5 verification tests."""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from omnigent.coordination.dispatcher import CoordinationDispatcher
from omnigent.coordination.store import CoordinationStore, StateTransitionConflict
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationRun,
    CoordinationTask,
    DeliveryAttempt,
)
from omnigent.db.db_models import SqlAgentMessage


@dataclass
class FakeConversation:
    id: str
    runner_id: str | None = None
    agent_id: str = "agent-test"


class FakeConversationStore:
    def __init__(self, convs: dict[str, FakeConversation]) -> None:
        self.convs = convs

    def get_conversation(self, conversation_id: str) -> FakeConversation | None:
        return self.convs.get(conversation_id)


class FakeRunnerResponse:
    def __init__(self, status_code: int = 200, text: str = "{}") -> None:
        self.status_code = status_code
        self.text = text


class FakeRunnerClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any], **_: Any) -> FakeRunnerResponse:
        self.calls.append({"url": url, "json": json})
        return FakeRunnerResponse(self.status_code, "{}")


class FakeRoutedRunner:
    def __init__(self, runner_id: str, client: FakeRunnerClient) -> None:
        self.runner_id = runner_id
        self.client = client


class FakeRunnerRouter:
    def __init__(self, status_code: int = 200) -> None:
        self.client = FakeRunnerClient(status_code)

    def client_for_session_resources(self, session_id: str) -> FakeRoutedRunner:
        return FakeRoutedRunner("runner_test", self.client)


@pytest.fixture
def memory_store(tmp_path) -> CoordinationStore:
    db_path = tmp_path / "test_coordination_fi.db"
    return CoordinationStore(f"sqlite:///{db_path}")


# ── Gate 1: Idempotency Key NOT NULL & Deduplication ─────────────


def test_g1_idempotency_key_not_null_enforced(memory_store: CoordinationStore) -> None:
    """G1: Database-level NOT NULL is enforced on agent_messages.idempotency_key."""
    with pytest.raises((IntegrityError, sa.exc.DBAPIError, sa.exc.PendingRollbackError)):
        with memory_store._session("test_not_null") as sess:
            msg_bad = SqlAgentMessage(
                message_id="msg_null_key",
                schema_version="1.0",
                root_session_id="root_sess",
                sender_session_id="s1",
                sender_role="debby",
                recipient_session_id="r1",
                kind="request",
                intent="task.request",
                idempotency_key=None,  # Forbidden by NOT NULL constraint!
                message_state="pending",
            )
            sess.add(msg_bad)


def test_g1_idempotent_duplicate_deduplication(memory_store: CoordinationStore) -> None:
    """G1: Re-dispatching with identical idempotency key deduplicates safely."""
    msg1 = AgentMessage(
        message_id="msg_first",
        root_session_id="root_1",
        sender_session_id="s_1",
        sender_role="debby",
        recipient_session_id="r_1",
        intent="task.request",
        idempotency_key="unique:op_100",
    )
    res1, out1 = memory_store.save_message_and_outbox(msg1)
    assert res1.message_id == "msg_first"
    assert out1.message_id == "msg_first"

    # Second insertion with same key should deduplicate to existing message
    msg2 = AgentMessage(
        message_id="msg_second",
        root_session_id="root_1",
        sender_session_id="s_1",
        sender_role="debby",
        recipient_session_id="r_1",
        intent="task.request",
        idempotency_key="unique:op_100",
    )
    res2, out2 = memory_store.save_message_and_outbox(msg2)
    assert res2.message_id == "msg_first"
    assert out2.message_id == "msg_first"


# ── Gate 2: Dispatcher Hard Pre-Check (Orphan Prevention) ────────


@pytest.mark.asyncio
async def test_g2_dispatcher_precheck_orphan_conversation_fails_immediately(
    memory_store: CoordinationStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2: Recipient conversation missing -> fails permanently immediately (0 retries)."""
    router = FakeRunnerRouter(status_code=200)
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: router,
    )

    conv_store = FakeConversationStore({})  # Empty: conversation does NOT exist
    dispatcher = CoordinationDispatcher(memory_store, conversation_store=conv_store)

    msg = AgentMessage(
        message_id="msg_orphan_target",
        root_session_id="root_1",
        sender_session_id="s_1",
        sender_role="debby",
        recipient_session_id="non_existent_session_id",
        intent="task.request",
        idempotency_key="idem:orphan_test",
    )
    memory_store.save_message_and_outbox(msg)

    # First dispatch should fail it permanently
    dispatched = await dispatcher.dispatch_once()
    assert dispatched == 1

    # Outbox should be marked confirmed/done (not requeued)
    outbox_items = memory_store.list_outbox_items(message_id=msg.message_id)
    assert len(outbox_items) == 1
    assert outbox_items[0].status == "failed"

    # Message state should be failed
    persisted_msg = memory_store.get_message(msg.message_id)
    assert persisted_msg is not None
    assert persisted_msg.message_state == "failed"

    # Attempt record must have CONVERSATION_NOT_FOUND error_code
    attempts = memory_store.list_delivery_attempts(msg.message_id)
    assert len(attempts) == 1
    assert attempts[0].delivery_state == "failed"
    assert attempts[0].error_code == "CONVERSATION_NOT_FOUND"
    assert attempts[0].attempt_count == 1

    # Next dispatch round: 0 items processed because orphan was not requeued!
    next_round = await dispatcher.dispatch_once()
    assert next_round == 0


@pytest.mark.asyncio
async def test_g2_dispatcher_precheck_runner_unbound_records_code(
    memory_store: CoordinationStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2: Recipient conversation exists but runner_id is None -> records RUNNER_UNBOUND."""
    router = FakeRunnerRouter(status_code=200)
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: router,
    )

    conv_store = FakeConversationStore(
        {
            "sess_unbound": FakeConversation(id="sess_unbound", runner_id=None),
        }
    )
    dispatcher = CoordinationDispatcher(memory_store, conversation_store=conv_store)

    msg = AgentMessage(
        message_id="msg_unbound_target",
        root_session_id="root_1",
        sender_session_id="s_1",
        sender_role="debby",
        recipient_session_id="sess_unbound",
        intent="task.request",
        idempotency_key="idem:unbound_test",
    )
    memory_store.save_message_and_outbox(msg)

    await dispatcher.dispatch_once()

    attempts = memory_store.list_delivery_attempts(msg.message_id)
    assert len(attempts) == 1
    assert attempts[0].delivery_state == "failed"
    assert attempts[0].error_code == "RUNNER_UNBOUND"
    outbox = memory_store.list_outbox_items(message_id=msg.message_id)[0]
    assert outbox.status == "pending"
    assert outbox.retry_count == 0
    assert outbox.next_retry_at > time.time()


# ── Gate 5: Structured error_code in delivery_attempts ────────────


@pytest.mark.asyncio
async def test_result_wakes_bound_origin_before_delivery(memory_store, monkeypatch):
    router = FakeRunnerRouter()
    recipient = FakeConversation(id="origin", runner_id=None)
    recipient.host_id = "host"  # type: ignore[attr-defined]
    conversations = FakeConversationStore({"origin": recipient})
    wakes = []

    async def recover(**kwargs):
        wakes.append(kwargs["session_id"])
        recipient.runner_id = "runner_test"

    async def relay(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router", lambda: router
    )
    monkeypatch.setattr(
        "omnigent.server.routes.sessions.routes_events._retry_session_single_flight", recover
    )
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.orchestration._ensure_runner_relay_ready", relay
    )
    message = AgentMessage(
        sender_session_id="polly",
        recipient_session_id="origin",
        intent="task.result",
        payload={"summary": "Complete"},
    )
    memory_store.save_message_and_outbox(message)
    dispatcher = CoordinationDispatcher(memory_store, conversations, app=SimpleNamespace())
    await dispatcher.dispatch_once()
    assert wakes == ["origin"]
    assert len(router.client.calls) == 1
    assert memory_store.list_outbox_items(message_id=message.message_id)[0].status == "confirmed"


def test_g5_error_code_column_and_sql_aggregation(memory_store: CoordinationStore) -> None:
    """G5: Ops can run SQL queries directly on delivery_attempts.error_code."""
    att1 = DeliveryAttempt(
        attempt_id="att_test_1",
        message_id="msg_1",
        target_session_id="sess_1",
        delivery_mode="offline",
        delivery_state="failed",
        error_code="RUNNER_OFFLINE",
        error="delivery-unreachable: runner is offline",
    )
    att2 = DeliveryAttempt(
        attempt_id="att_test_2",
        message_id="msg_2",
        target_session_id="sess_2",
        delivery_mode="offline",
        delivery_state="failed",
        error_code="RUNNER_UNBOUND",
        error="delivery-unreachable: conversation not bound",
    )
    att3 = DeliveryAttempt(
        attempt_id="att_test_3",
        message_id="msg_3",
        target_session_id="sess_3",
        delivery_mode="offline",
        delivery_state="failed",
        error_code="RUNNER_OFFLINE",
        error="delivery-unreachable: runner is offline",
    )
    memory_store.record_delivery_attempt(att1)
    memory_store.record_delivery_attempt(att2)
    memory_store.record_delivery_attempt(att3)

    # Verify domain retrieval preserves error_code
    attempts = memory_store.list_delivery_attempts("msg_1")
    assert len(attempts) == 1
    assert attempts[0].error_code == "RUNNER_OFFLINE"

    # Verify direct SQL query aggregation works for SRE/Ops without zstd decompression
    with memory_store._session("sql_ops") as sess:
        res = sess.execute(
            sa.text(
                "SELECT error_code, COUNT(*) as cnt FROM delivery_attempts "
                "GROUP BY error_code ORDER BY cnt DESC"
            )
        ).fetchall()
        counts = {row[0]: row[1] for row in res}
        assert counts.get("RUNNER_OFFLINE") == 2
        assert counts.get("RUNNER_UNBOUND") == 1


# ── Gate 4: Fault Injection & Crash Recovery ─────────────────────


def test_g4_task_deadline_recovery_and_fencing_conflict(memory_store: CoordinationStore) -> None:
    """G4: Expired tasks reclaimed; late-recovering workers cannot double-commit."""
    run = CoordinationRun(run_id="run_fi", root_session_id="root_fi")
    memory_store.create_run(run)

    # Task created with expired deadline in the past
    expired_time = time.time() - 30.0
    task = CoordinationTask(
        task_id="task_worker_crash",
        run_id="run_fi",
        title="critical-task",
        status="running",
        deadline=expired_time,
        assignee_session_id="worker_crashed_sess",
    )
    memory_store.create_task(task)

    # 1. Harvest expired tasks
    expired_tasks = memory_store.list_expired_active_tasks(now=time.time())
    assert len(expired_tasks) >= 1
    assert any(t.task_id == "task_worker_crash" for t in expired_tasks)

    # 2. Coordinator detects crash and transitions task to "failed" or reassigns
    transitioned = memory_store.transition_task_status(
        "task_worker_crash",
        "failed",
        from_statuses=["running"],
    )
    assert transitioned is True

    # 3. Old crashed worker wakes up late after deadline and attempts to commit "completed"
    # StateTransitionConflict is raised by the CAS boundary — fencing protection works!
    with pytest.raises(StateTransitionConflict):
        memory_store.transition_task_status(
            "task_worker_crash",
            "completed",
            from_statuses=["running"],
        )
