"""Tests for Multi-Agent Coordination Data Layer and Outbox."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omnigent.coordination.dispatcher import CoordinationDispatcher
from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationRun,
    CoordinationTask,
)
from omnigent.coordination.workflow_engine import CoordinationWorkflowEngine
from omnigent.server.routes.coordination import router
from omnigent.workspaces.lease import WorkspaceCoordinator, WorkspaceLeaseManager


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


def test_workspace_lease_manager(tmp_path: Path) -> None:
    mgr = WorkspaceLeaseManager()
    ws_path = tmp_path / "repo"

    # 1. Acquire write lease
    lease1 = mgr.acquire(ws_path, "session_coder_1", mode="write", duration_s=100.0)
    assert lease1.holder_session_id == "session_coder_1"
    assert lease1.mode == "write"

    # 2. Conflicting write lease from another session is rejected
    with pytest.raises(RuntimeError, match="locked by session"):
        mgr.acquire(ws_path, "session_coder_2", mode="write")

    # 3. Release and re-acquire
    assert mgr.release(ws_path, "session_coder_1") is True
    lease2 = mgr.acquire(ws_path, "session_coder_2", mode="write")
    assert lease2.holder_session_id == "session_coder_2"


@pytest.mark.asyncio
async def test_plan_implement_review_workflow_engine(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Refactor Auth Module",
        root_session_id="conv_root_flow",
        planner_session_id="conv_planner_1",
        implementer_session_id="conv_coder_1",
        reviewer_session_id="conv_reviewer_1",
        user_prompt="Add JWT refresh token rotation",
        workspace_path=str(tmp_path),
    )
    assert run.run_id.startswith("run_")
    assert run.status == "running"

    tasks = memory_store.list_tasks(run.run_id)
    assert len(tasks) == 3
    assert tasks[0].assignee_role == "planner"
    assert tasks[1].assignee_role == "implementer"
    assert tasks[2].assignee_role == "reviewer"
    assert tasks[1].dependencies == [tasks[0].task_id]


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

    # 4. Acquire Workspace Lease
    res_lease = client.post(
        "/v1/coordination/workspaces/lease",
        json={"workspace_path": "/tmp/test-repo", "holder_session_id": "conv_c1", "mode": "write"},
    )
    assert res_lease.status_code == 200
    assert res_lease.json()["lease"]["holder_session_id"] == "conv_c1"

    # 5. Start Workflow Run
    res_wf = client.post(
        "/v1/coordination/workflows/plan-implement-review",
        json={
            "title": "API Full Run",
            "root_session_id": "conv_root_api",
            "planner_session_id": "conv_p1",
            "implementer_session_id": "conv_c1",
            "reviewer_session_id": "conv_r1",
            "user_prompt": "Fix cache bug",
        },
    )
    assert res_wf.status_code == 200
    assert len(res_wf.json()["tasks"]) == 3
