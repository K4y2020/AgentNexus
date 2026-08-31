"""Unit and integration tests for AgentNexus Multi-Agent Coordination Data Layer and Outbox."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omnigent.coordination.dispatcher import CoordinationDispatcher
from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationEvent,
    CoordinationRun,
    CoordinationTask,
)
from omnigent.server.routes.coordination import router


from pathlib import Path

@pytest.fixture
def memory_store(tmp_path: Path) -> CoordinationStore:
    return CoordinationStore(tmp_path / "coord_test.db")


def test_coordination_run_and_task_crud(memory_store: CoordinationStore) -> None:
    # 1. Create and get Run
    run = CoordinationRun(
        title="Test Multi-Agent Run",
        root_session_id="conv_root_123",
        template="plan_implement_review",
        budget={"max_turns": 20},
    )
    created_run = memory_store.create_run(run)
    assert created_run.run_id.startswith("run_")

    fetched_run = memory_store.get_run(run.run_id)
    assert fetched_run is not None
    assert fetched_run.title == "Test Multi-Agent Run"
    assert fetched_run.budget["max_turns"] == 20

    # 2. Create Tasks
    task1 = CoordinationTask(
        run_id=run.run_id,
        title="Write Plan Artifact",
        assignee_role="planner",
    )
    task2 = CoordinationTask(
        run_id=run.run_id,
        title="Implement Code",
        assignee_role="implementer",
        dependencies=[task1.task_id],
    )
    memory_store.create_task(task1)
    memory_store.create_task(task2)

    tasks = memory_store.list_tasks(run.run_id)
    assert len(tasks) == 2
    assert tasks[0].title == "Write Plan Artifact"
    assert tasks[1].dependencies == [task1.task_id]


def test_atomic_message_and_outbox_persistence(memory_store: CoordinationStore) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_123",
        sender_session_id="conv_planner",
        sender_role="planner",
        recipient_session_id="conv_coder",
        recipient_role="implementer",
        intent="task.request",
        payload={"instruction": "Implement feature X"},
        idempotency_key="idem_001",
    )
    saved_msg, outbox = memory_store.save_message_and_outbox(msg)

    assert saved_msg.message_id.startswith("msg_")
    assert outbox.item_id.startswith("out_")
    assert outbox.status == "pending"

    # Outbox polling
    pending = memory_store.fetch_pending_outbox()
    assert len(pending) == 1
    assert pending[0].message_id == saved_msg.message_id

    # Messages query
    msgs = memory_store.list_messages("conv_root_123")
    assert len(msgs) == 1
    assert msgs[0].payload["instruction"] == "Implement feature X"


@pytest.mark.asyncio
async def test_dispatcher_delivers_and_records_attempt(memory_store: CoordinationStore) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_123",
        sender_session_id="conv_planner",
        recipient_session_id="conv_coder",
        intent="review.request",
        payload={"diff_url": "artifact://diff_001"},
    )
    memory_store.save_message_and_outbox(msg)

    dispatcher = CoordinationDispatcher(memory_store)
    count = await dispatcher.dispatch_once()
    assert count == 1

    # Outbox should no longer be pending
    pending = memory_store.fetch_pending_outbox()
    assert len(pending) == 0


def test_coordination_events_audit_timeline(memory_store: CoordinationStore) -> None:
    evt1 = CoordinationEvent(
        root_session_id="conv_root_123",
        event_type="run.started",
        payload={"template": "plan_implement_review"},
        created_at=100.0,
    )
    evt2 = CoordinationEvent(
        root_session_id="conv_root_123",
        event_type="task.completed",
        payload={"task_id": "ctask_1"},
        created_at=200.0,
    )
    memory_store.record_event(evt1)
    memory_store.record_event(evt2)

    events_all = memory_store.list_events("conv_root_123")
    assert len(events_all) == 2

    events_since = memory_store.list_events("conv_root_123", since=150.0)
    assert len(events_since) == 1
    assert events_since[0].event_type == "task.completed"


def test_coordination_api_endpoints() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. Create Run
    res_run = client.post(
        "/v1/coordination/runs",
        json={"title": "E2E Run", "root_session_id": "conv_root_api", "template": "standard"},
    )
    assert res_run.status_code == 200
    run_data = res_run.json()["run"]
    run_id = run_data["run_id"]

    # 2. Create Task
    res_task = client.post(
        "/v1/coordination/tasks",
        json={"run_id": run_id, "title": "API Task 1", "assignee_role": "implementer"},
    )
    assert res_task.status_code == 200
    assert res_task.json()["task"]["title"] == "API Task 1"

    # 3. Send Message
    res_msg = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "conv_p1",
            "recipient_session_id": "conv_c1",
            "sender_role": "planner",
            "intent": "task.request",
            "payload": {"prompt": "Run build"},
        },
    )
    assert res_msg.status_code == 200
    assert res_msg.json()["delivery_state"] == "pending"

    # 4. List Messages
    res_list = client.get("/v1/coordination/messages?root_session_id=conv_root_api")
    assert res_list.status_code == 200
    assert len(res_list.json()["messages"]) >= 1

    # 5. List Events
    res_events = client.get("/v1/coordination/events?root_session_id=conv_root_api")
    assert res_events.status_code == 200
    assert len(res_events.json()["events"]) >= 1
