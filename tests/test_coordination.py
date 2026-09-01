"""Tests for Multi-Agent Coordination Data Layer, Outbox, ACL, and Workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.coordination.dispatcher import CoordinationDispatcher
from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationRun,
    CoordinationTask,
)
from omnigent.coordination.workflow_engine import CoordinationWorkflowEngine
from omnigent.db.db_models import InvalidUuidError
from omnigent.server.routes.coordination import router
from omnigent.workspaces.lease import WorkspaceCoordinator, WorkspaceLeaseManager


@dataclass
class FakeConversation:
    id: str
    root_conversation_id: str | None = None
    parent_conversation_id: str | None = None


class FakeConversationStore:
    def __init__(self, conversations: dict[str, FakeConversation]) -> None:
        self.conversations = conversations

    def get_conversation(self, conversation_id: str) -> FakeConversation | None:
        return self.conversations.get(conversation_id)


class FakeRunnerResponse:
    status_code: int
    text: str

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class FakeRunnerClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.posted: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any], **_: Any) -> FakeRunnerResponse:
        self.posted.append({"url": url, "json": json})
        return FakeRunnerResponse(self.status_code, "{}")


class FakeRoutedRunner:
    def __init__(self, runner_id: str, client: FakeRunnerClient) -> None:
        self.runner_id = runner_id
        self.client = client


class FakeRunnerRouter:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.client = FakeRunnerClient(status_code)
        self.called_session_ids: list[str] = []

    def client_for_session_resources(self, session_id: str) -> FakeRoutedRunner:
        self.called_session_ids.append(session_id)
        return FakeRoutedRunner("runner_abc", self.client)


@pytest.fixture
def memory_store(tmp_path: Path) -> CoordinationStore:
    return CoordinationStore(tmp_path / "coord_test.db")


@pytest.fixture
def default_conversations() -> dict[str, FakeConversation]:
    root_id = "conv_root_api"
    return {
        root_id: FakeConversation(root_id, root_conversation_id=root_id),
        "conv_p1": FakeConversation("conv_p1", root_conversation_id=root_id),
        "conv_c1": FakeConversation("conv_c1", root_conversation_id=root_id),
        "conv_r1": FakeConversation("conv_r1", root_conversation_id=root_id),
        "other_root": FakeConversation("other_root", root_conversation_id="other_root"),
        "other_sender": FakeConversation(
            "other_sender", root_conversation_id="other_root"
        ),
    }


def make_api_app(
    store: CoordinationStore,
    conversations: dict[str, FakeConversation],
) -> FastAPI:
    app = FastAPI()
    app.state.coordination_store = store
    app.state.conversation_store = FakeConversationStore(conversations)
    app.state.workspace_lease_manager = WorkspaceLeaseManager(store)
    app.state.workspace_coordinator = WorkspaceCoordinator(
        app.state.workspace_lease_manager
    )
    app.state.coordination_workflow_engine = CoordinationWorkflowEngine(
        store, app.state.workspace_coordinator
    )
    app.include_router(router)
    return app


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
async def test_dispatcher_confirms_only_after_runner_2xx(
    memory_store: CoordinationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_123",
        sender_session_id="conv_planner",
        sender_role="planner",
        recipient_session_id="conv_coder",
        recipient_role="implementer",
        intent="review.request",
        payload={"diff_url": "artifact://diff_001"},
    )
    memory_store.save_message_and_outbox(msg)

    router = FakeRunnerRouter(status_code=200)
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: router,
    )
    dispatcher = CoordinationDispatcher(memory_store)
    count = await dispatcher.dispatch_once()
    assert count == 1

    item = memory_store.get_outbox_item(
        memory_store.list_outbox_items(message_id=msg.message_id)[0].item_id
    )
    assert item is not None
    assert item.status == "confirmed"
    assert memory_store.get_message(msg.message_id) is not None
    assert memory_store.get_message(msg.message_id).message_state == "active"

    attempts = memory_store.list_delivery_attempts(msg.message_id)
    assert len(attempts) == 1
    assert attempts[0].delivery_state == "confirmed"
    assert attempts[0].injection_receipt is not None

    posted = router.client.posted
    assert len(posted) == 1
    body = posted[0]["json"]
    assert body["metadata"]["a2a"] is True
    assert body["metadata"]["message_id"] == msg.message_id
    assert body["metadata"]["sender_session_id"] == msg.sender_session_id
    assert "review.request" in body["content"][0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_failure_stays_unconfirmed_and_requeues(
    memory_store: CoordinationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_123",
        sender_session_id="conv_planner",
        recipient_session_id="conv_coder",
        intent="task.request",
        payload={"prompt": "Do the thing"},
    )
    memory_store.save_message_and_outbox(msg)

    class RejectingRouter(FakeRunnerRouter):
        def client_for_session_resources(self, session_id: str) -> FakeRoutedRunner:
            self.called_session_ids.append(session_id)
            return FakeRoutedRunner("runner_abc", FakeRunnerClient(status_code=503))

    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: RejectingRouter(),
    )
    dispatcher = CoordinationDispatcher(memory_store)
    count = await dispatcher.dispatch_once()
    assert count == 1

    item = memory_store.get_outbox_item(
        memory_store.list_outbox_items(message_id=msg.message_id)[0].item_id
    )
    assert item is not None
    assert item.status == "pending"
    assert item.retry_count == 1
    assert memory_store.get_message(msg.message_id).message_state == "queued"

    attempts = memory_store.list_delivery_attempts(msg.message_id)
    assert len(attempts) == 1
    assert attempts[0].delivery_state == "failed"
    assert "status 503" in (attempts[0].error or "")


@pytest.mark.asyncio
async def test_dispatcher_invalid_recipient_fails_permanently(
    memory_store: CoordinationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_123",
        sender_session_id="conv_planner",
        recipient_session_id="not-a-valid-session-id",
        intent="task.request",
        payload={"prompt": "no target"},
    )
    memory_store.save_message_and_outbox(msg)

    class InvalidRouter(FakeRunnerRouter):
        def client_for_session_resources(self, session_id: str) -> FakeRoutedRunner:
            raise InvalidUuidError("expected a 32-char hex uuid")

    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: InvalidRouter(),
    )
    dispatcher = CoordinationDispatcher(memory_store)
    count = await dispatcher.dispatch_once()
    assert count == 1

    item = memory_store.get_outbox_item(
        memory_store.list_outbox_items(message_id=msg.message_id)[0].item_id
    )
    assert item is not None
    assert item.status == "failed"
    assert item.retry_count == 0
    attempts = memory_store.list_delivery_attempts(msg.message_id)
    assert len(attempts) == 1
    assert attempts[0].delivery_state == "failed"
    assert "invalid recipient" in (attempts[0].error or "")


def test_workspace_lease_manager_persists_restart(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    ws_path = tmp_path / "repo"

    # 1. Acquire write lease through the persistent manager
    mgr = WorkspaceLeaseManager(memory_store)
    lease1 = mgr.acquire(ws_path, "session_coder_1", mode="write", duration_s=100.0)
    assert lease1.holder_session_id == "session_coder_1"
    assert lease1.mode == "write"

    # 2. A brand-new manager backed by the same store sees the active lease
    mgr2 = WorkspaceLeaseManager(memory_store)
    restored = mgr2.get_lease(ws_path)
    assert restored is not None
    assert restored.holder_session_id == "session_coder_1"
    assert restored.fencing_token == lease1.fencing_token

    # 3. Conflicting write lease from another session is rejected
    with pytest.raises(RuntimeError, match="locked by session"):
        mgr2.acquire(ws_path, "session_coder_2", mode="write")

    # 4. Release and re-acquire, fencing token must increase
    assert mgr2.release(ws_path, "session_coder_1") is True
    lease2 = mgr2.acquire(ws_path, "session_coder_2", mode="write")
    assert lease2.holder_session_id == "session_coder_2"
    assert lease2.fencing_token > lease1.fencing_token


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
    assert len(tasks) == 5
    assert [t.assignee_role for t in tasks] == [
        "planner",
        "implementer",
        "reviewer",
        "fixer",
        "tester",
    ]
    assert tasks[0].assignee_role == "planner"
    assert tasks[1].assignee_role == "implementer"
    assert tasks[2].assignee_role == "reviewer"
    assert tasks[1].dependencies == [tasks[0].task_id]


@pytest.mark.asyncio
async def test_workflow_advances_through_five_stages_to_success(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Auth Module Flow",
        root_session_id="conv_root_flow_ok",
        planner_session_id="conv_planner_1",
        implementer_session_id="conv_coder_1",
        reviewer_session_id="conv_reviewer_1",
        user_prompt="Add token rotation",
        workspace_path=str(tmp_path),
    )
    tasks = memory_store.list_tasks(run.run_id)
    by_role = {t.assignee_role: t for t in tasks}

    updated = await engine.advance(
        run_id=run.run_id,
        task_id=by_role["planner"].task_id,
        outcome="succeeded",
        artifacts=[{"name": "plan.md"}],
    )
    assert updated.status == "running"
    impl = memory_store.get_task(by_role["implementer"].task_id)
    assert impl is not None
    assert impl.status == "running"

    updated = await engine.advance(
        run_id=run.run_id,
        task_id=by_role["implementer"].task_id,
        outcome="succeeded",
        artifacts=[{"name": "patch.diff"}],
    )
    assert updated.status == "running"
    reviewer = memory_store.get_task(by_role["reviewer"].task_id)
    assert reviewer is not None
    assert reviewer.status == "running"

    updated = await engine.advance(
        run_id=run.run_id,
        task_id=by_role["reviewer"].task_id,
        outcome="succeeded",
        review_decision="approved",
    )
    assert updated.status == "running"
    assert memory_store.get_task(by_role["fixer"].task_id).status == "succeeded"
    assert memory_store.get_task(by_role["tester"].task_id).status == "running"

    completed = await engine.advance(
        run_id=run.run_id,
        task_id=by_role["tester"].task_id,
        outcome="succeeded",
        artifacts=[{"name": "test-report.md"}],
    )
    assert completed.status == "succeeded"


@pytest.mark.asyncio
async def test_workflow_changes_requested_loops_through_fixer(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Auth Module Review Loop",
        root_session_id="conv_root_flow_review",
        planner_session_id="conv_planner_1",
        implementer_session_id="conv_coder_1",
        reviewer_session_id="conv_reviewer_1",
        user_prompt="Add token rotation",
        workspace_path=str(tmp_path),
    )
    tasks = memory_store.list_tasks(run.run_id)
    by_role = {t.assignee_role: t for t in tasks}

    await engine.advance(
        run_id=run.run_id,
        task_id=by_role["planner"].task_id,
        outcome="succeeded",
    )
    await engine.advance(
        run_id=run.run_id,
        task_id=by_role["implementer"].task_id,
        outcome="succeeded",
    )
    updated = await engine.advance(
        run_id=run.run_id,
        task_id=by_role["reviewer"].task_id,
        outcome="succeeded",
        review_decision="changes_requested",
    )
    assert updated.status == "running"
    fixer = memory_store.get_task(by_role["fixer"].task_id)
    assert fixer is not None
    assert fixer.status == "running"
    # The reviewer record stays "waiting_review" until a re-review lands.
    reviewer = memory_store.get_task(by_role["reviewer"].task_id)
    assert reviewer is not None
    assert reviewer.status == "waiting_review"

    await engine.advance(
        run_id=run.run_id,
        task_id=by_role["fixer"].task_id,
        outcome="succeeded",
        artifacts=[{"name": "fix.diff"}],
    )
    assert memory_store.get_task(by_role["reviewer"].task_id).status == "running"

    updated = await engine.advance(
        run_id=run.run_id,
        task_id=by_role["reviewer"].task_id,
        outcome="succeeded",
        review_decision="approved",
    )
    assert updated.status == "running"
    assert memory_store.get_task(by_role["tester"].task_id).status == "running"

    completed = await engine.advance(
        run_id=run.run_id,
        task_id=by_role["tester"].task_id,
        outcome="succeeded",
    )
    assert completed.status == "succeeded"


def test_coordination_api_endpoints(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
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
        json={
            "root_session_id": "conv_root_api",
            "workspace_path": "/tmp/test-repo",
            "holder_session_id": "conv_c1",
            "mode": "write",
        },
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
    wf_data = res_wf.json()
    assert len(wf_data["tasks"]) == 5
    tasks_by_role = {t["assignee_role"]: t["task_id"] for t in wf_data["tasks"]}

    # 6. Drive the fixed workflow through its stage reports.
    for role, review_decision in (
        ("planner", None),
        ("implementer", None),
        ("reviewer", "approved"),
        ("tester", None),
    ):
        body: dict[str, Any] = {"outcome": "succeeded"}
        if review_decision:
            body["review_decision"] = review_decision
        res_adv = client.post(
            f"/v1/coordination/workflows/{wf_data['run']['run_id']}/tasks/"
            f"{tasks_by_role[role]}/advance",
            json=body,
        )
        assert res_adv.status_code == 200, res_adv.text
    final_run = client.get(
        f"/v1/coordination/runs/{wf_data['run']['run_id']}"
    ).json()["run"]
    assert final_run["status"] == "succeeded"


def test_coordination_api_session_acl(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    # Cross-tree sender is rejected: the requester cannot forge another agent
    # identity outside the root they claim.
    res = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "other_sender",
            "recipient_session_id": "conv_c1",
            "sender_role": "planner",
            "intent": "task.request",
            "payload": {"prompt": "cross tree"},
        },
    )
    assert res.status_code == 403
    assert "does not belong" in res.json()["detail"]

    # Unknown root fails closed even when the store is available.
    res2 = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "missing_root",
            "sender_session_id": "conv_p1",
            "recipient_session_id": "conv_c1",
            "sender_role": "planner",
            "intent": "task.request",
            "payload": {"prompt": "bad root"},
        },
    )
    assert res2.status_code == 404

    # Unknown sender inside a real root is also rejected.
    res3 = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "ghost",
            "recipient_session_id": "conv_c1",
            "sender_role": "planner",
            "intent": "task.request",
            "payload": {"prompt": "ghost sender"},
        },
    )
    assert res3.status_code == 404

    # A run cannot be created under a root that does not exist.
    res4 = client.post(
        "/v1/coordination/runs",
        json={"title": "Missing root", "root_session_id": "missing_root", "template": "standard"},
    )
    assert res4.status_code == 404
