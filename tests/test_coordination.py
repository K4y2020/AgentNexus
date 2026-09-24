"""Tests for Multi-Agent Coordination Data Layer, Outbox, ACL, and Workflow."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agentnexus.coordination.dispatcher import CoordinationDispatcher
from agentnexus.coordination.policy_gate import CoordinationPolicyGate
from agentnexus.coordination.reconciliation import reconcile_effect_unknown
from agentnexus.coordination.store import CoordinationStore
from agentnexus.coordination.types import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MAX_PAYLOAD_BYTES,
    AgentMessage,
    CoordinationArtifact,
    CoordinationRun,
    CoordinationTask,
    DeliveryAttempt,
)
from agentnexus.coordination.workflow_engine import (
    CoordinationWorkflowEngine,
    WorkflowDagTaskSpec,
)
from agentnexus.coordination.workflow_scheduler import CoordinationWorkflowScheduler
from agentnexus.db.db_models import InvalidUuidError
from agentnexus.debug_logging import current_user_id_scope
from agentnexus.errors import AgentNexusError, ErrorCode
from agentnexus.policies.types import PolicyResult
from agentnexus.server.routes.coordination import (
    _authorized_runs,
    _require_coordination_acl,
    router,
)
from agentnexus.spec.types import PolicyAction
from agentnexus.workspaces.lease import WorkspaceCoordinator, WorkspaceLeaseManager


@dataclass
class FakeConversation:
    id: str
    root_conversation_id: str | None = None
    parent_conversation_id: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    runner_id: str | None = None
    agent_id: str | None = None


class FakeConversationStore:
    def __init__(self, conversations: dict[str, FakeConversation]) -> None:
        self.conversations = conversations

    def get_conversation(self, conversation_id: str) -> FakeConversation | None:
        return self.conversations.get(conversation_id)


class FakePermissionStore:
    """Minimal stand-in for session access grants used by coordination ACL tests."""

    def __init__(self, grants: dict[tuple[str, str], int], admin: bool = False) -> None:
        self.grants = grants
        self.admin = admin

    def is_admin(self, user_id: str | None) -> bool:
        return bool(user_id) and self.admin

    def check_access(
        self,
        user_id: str | None,
        conversation_id: str,
        required_level: int,
    ) -> bool:
        if user_id is None:
            return False
        return self.grants.get((user_id, conversation_id), 0) >= required_level


def _acl_request(app: FastAPI, method: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/v1/coordination/messages",
            "headers": [],
            "app": app,
        }
    )


@pytest.mark.asyncio
async def test_coordination_acl_requires_manage_for_mutating_requests() -> None:
    conversations = {
        "conv_root": FakeConversation("conv_root", root_conversation_id="conv_root"),
        "conv_child": FakeConversation("conv_child", root_conversation_id="conv_root"),
    }
    app = FastAPI()
    app.state.conversation_store = FakeConversationStore(conversations)
    app.state.permission_store = FakePermissionStore(
        {
            ("alice", "conv_root"): 1,
            ("alice", "conv_child"): 1,
        }
    )

    with current_user_id_scope("alice"):
        await _require_coordination_acl(
            _acl_request(app, "GET"),
            "conv_root",
            "conv_child",
        )
        with pytest.raises(AgentNexusError) as exc:
            await _require_coordination_acl(
                _acl_request(app, "POST"),
                "conv_root",
                "conv_child",
            )
    assert exc.value.code == ErrorCode.FORBIDDEN

    app.state.permission_store.grants[("alice", "conv_root")] = 3
    app.state.permission_store.grants[("alice", "conv_child")] = 3
    with current_user_id_scope("alice"):
        await _require_coordination_acl(
            _acl_request(app, "POST"),
            "conv_root",
            "conv_child",
        )


@pytest.mark.asyncio
async def test_authorized_runs_hides_roots_the_caller_cannot_read() -> None:
    """An unfiltered run listing spans the workspace, so each run is checked."""
    app = FastAPI()
    app.state.conversation_store = FakeConversationStore(
        {
            "conv_root": FakeConversation("conv_root", root_conversation_id="conv_root"),
            "conv_other": FakeConversation("conv_other", root_conversation_id="conv_other"),
        }
    )
    app.state.permission_store = FakePermissionStore({("alice", "conv_root"): 3})
    runs = [
        CoordinationRun(run_id="run_alice", root_session_id="conv_root"),
        CoordinationRun(run_id="run_other", root_session_id="conv_other"),
    ]

    with current_user_id_scope("alice"):
        allowed = await _authorized_runs(_acl_request(app, "GET"), runs)

    assert [run.run_id for run in allowed] == ["run_alice"]


@pytest.mark.asyncio
async def test_authorized_runs_passes_through_without_a_permission_store() -> None:
    """Single-user deployments have no permission store and see every run."""
    app = FastAPI()
    app.state.conversation_store = FakeConversationStore({})
    runs = [CoordinationRun(run_id="run_only", root_session_id="conv_root")]

    with current_user_id_scope("alice"):
        allowed = await _authorized_runs(_acl_request(app, "GET"), runs)

    assert [run.run_id for run in allowed] == ["run_only"]


def test_coordination_api_run_list_hides_unreadable_roots(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint filters, not just the helper it delegates to.

    Listing without ``root_session_id`` used to take a branch with no access
    check at all, returning every run in the workspace to any caller.
    """
    memory_store.create_run(CoordinationRun(run_id="run_alice", root_session_id="conv_root_api"))
    memory_store.create_run(CoordinationRun(run_id="run_bob", root_session_id="conv_p1"))

    app = make_api_app(memory_store, default_conversations)
    app.state.permission_store = FakePermissionStore({("alice", "conv_root_api"): 3})
    monkeypatch.setenv("AGENTNEXUS_USER_ID", "alice")

    res = TestClient(app).get("/v1/coordination/runs")

    assert res.status_code == 200
    assert [run["run_id"] for run in res.json()["runs"]] == ["run_alice"]


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


class FixedPolicyEngine:
    """Minimal engine double returning one composed verdict."""

    def __init__(self, result: PolicyResult) -> None:
        self.result = result

    async def evaluate(self, _ctx: Any, **_kwargs: Any) -> PolicyResult:
        return self.result


class RecordingPolicyGateFactory:
    """Engine factory that records stage order and returns fixed veredicts."""

    def __init__(
        self,
        results: dict[str, PolicyResult] | None = None,
        *,
        default: PolicyResult | None = None,
    ) -> None:
        self.results = results or {}
        self.default = default or PolicyResult(action=PolicyAction.ALLOW)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, stage: str, session_id: str):
        self.calls.append((stage, session_id))
        return FixedPolicyEngine(self.results.get(stage, self.default))


@pytest.fixture
def memory_store(tmp_path: Path) -> CoordinationStore:
    return CoordinationStore(tmp_path / "coord_test.db")


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "managed"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def default_conversations(workspace_root: Path) -> dict[str, FakeConversation]:
    root_id = "conv_root_api"
    managed = str(workspace_root)
    return {
        root_id: FakeConversation(
            root_id,
            root_conversation_id=root_id,
            host_id="host_ws_test",
            workspace=managed,
        ),
        "conv_p1": FakeConversation(
            "conv_p1", root_conversation_id=root_id, host_id="host_ws_test", workspace=managed
        ),
        "conv_p2": FakeConversation(
            "conv_p2", root_conversation_id=root_id, host_id="host_ws_test", workspace=managed
        ),
        "conv_c1": FakeConversation(
            "conv_c1", root_conversation_id=root_id, host_id="host_ws_test", workspace=managed
        ),
        "conv_r1": FakeConversation(
            "conv_r1", root_conversation_id=root_id, host_id="host_ws_test", workspace=managed
        ),
        "other_root": FakeConversation(
            "other_root",
            root_conversation_id="other_root",
            host_id="host_ws_test",
            workspace=managed,
        ),
        "other_sender": FakeConversation(
            "other_sender",
            root_conversation_id="other_root",
            host_id="host_ws_test",
            workspace=managed,
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
    app.state.workspace_coordinator = WorkspaceCoordinator(app.state.workspace_lease_manager)
    app.state.coordination_workflow_engine = CoordinationWorkflowEngine(
        store, app.state.workspace_coordinator
    )
    app.include_router(router)
    return app


def init_merge_repo(tmp_path: Path, *, feature_change: str = "base\nfeature\n") -> Path:
    """Create a tiny main + feature git repo with feature checked out then main restored."""
    repo = tmp_path / "managed" / "repo"
    repo.mkdir(parents=True)

    def git(*args: str, cwd: Path = repo) -> subprocess.CompletedProcess[str]:
        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
        return res

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test User")
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    git("switch", "-c", "feature")
    (repo / "a.txt").write_text(feature_change, encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "feature")
    git("switch", "main")
    return repo


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
        acceptance_criteria=["Plan is clear and actionable"],
        deadline=1234.0,
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
    assert tasks[0].acceptance_criteria == ["Plan is clear and actionable"]
    assert tasks[0].deadline == 1234.0
    assert tasks[1].dependencies == [task1.task_id]
    fetched = memory_store.get_task(task1.task_id)
    assert fetched is not None
    assert fetched.acceptance_criteria == ["Plan is clear and actionable"]
    assert fetched.deadline == 1234.0


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
    assert saved_msg.hop_count == 0
    assert saved_msg.max_hops == DEFAULT_MAX_HOPS
    assert saved_msg.ttl_seconds is None
    assert outbox.item_id.startswith("out_")
    assert outbox.status == "pending"
    assert outbox.target_sequence == 1

    # Outbox polling
    pending = memory_store.fetch_pending_outbox()
    assert len(pending) == 1
    assert pending[0].message_id == saved_msg.message_id

    # Messages query
    msgs = memory_store.list_messages("conv_root_123")
    assert len(msgs) == 1
    assert msgs[0].payload["instruction"] == "Implement feature X"

    msg2 = AgentMessage(
        root_session_id="conv_root_123",
        sender_session_id="conv_planner",
        recipient_session_id="conv_coder",
        intent="task.request",
        payload={"instruction": "Next item"},
        hop_count=2,
        max_hops=5,
        ttl_seconds=60.0,
    )
    _, outbox2 = memory_store.save_message_and_outbox(msg2)
    assert outbox2.target_sequence == 2
    reloaded = memory_store.list_messages("conv_root_123")[1]
    assert reloaded.hop_count == 2
    assert reloaded.max_hops == 5
    assert reloaded.ttl_seconds == 60.0


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
        artifacts=[{"type": "input_file", "file_id": "file_target", "filename": "plan.md"}],
    )
    memory_store.save_message_and_outbox(msg)

    router = FakeRunnerRouter(status_code=200)
    monkeypatch.setattr(
        "agentnexus.server.routes._sessions.common.get_server_runner_router",
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
    assert attempts[0].target_sequence == 1
    assert attempts[0].injection_receipt is not None
    assert memory_store.get_message(msg.message_id).consumption_state == "unconsumed"

    posted = router.client.posted
    assert len(posted) == 1
    body = posted[0]["json"]
    assert body["metadata"]["a2a"] is True
    assert body["metadata"]["message_id"] == msg.message_id
    assert body["metadata"]["sender_session_id"] == msg.sender_session_id
    assert "review.request" in body["content"][0]["text"]
    assert body["content"][1] == {
        "type": "input_file",
        "file_id": "file_target",
        "filename": "plan.md",
    }


@pytest.mark.asyncio
async def test_dispatcher_rides_recipient_agent_and_relay_into_delivery(
    memory_store: CoordinationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_123",
        sender_session_id="conv_planner",
        sender_role="planner",
        recipient_session_id="conv_coder",
        recipient_role="implementer",
        intent="task.request",
        payload={"prompt": "Implement the plan"},
    )
    memory_store.save_message_and_outbox(msg)

    router = FakeRunnerRouter(status_code=200)
    monkeypatch.setattr(
        "agentnexus.server.routes._sessions.common.get_server_runner_router",
        lambda: router,
    )
    relay_calls: list[tuple[str, str | None, FakeRunnerClient]] = []

    async def fake_relay(
        session_id: str,
        runner_id: str | None,
        client: FakeRunnerClient,
        store: Any,
    ) -> None:
        relay_calls.append((session_id, runner_id, client))

    monkeypatch.setattr(
        "agentnexus.server.routes._sessions.orchestration._ensure_runner_relay_ready",
        fake_relay,
    )
    conv_store = FakeConversationStore(
        {
            "conv_coder": FakeConversation(
                "conv_coder",
                runner_id="runner_abc",
                agent_id="ag_planner",
            )
        }
    )
    dispatcher = CoordinationDispatcher(memory_store, conversation_store=conv_store)
    count = await dispatcher.dispatch_once()
    assert count == 1

    body = router.client.posted[0]["json"]
    assert body["agent_id"] == "ag_planner"
    assert body["model"] == "ag_planner"
    assert relay_calls == [("conv_coder", "runner_abc", router.client)]


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
        "agentnexus.server.routes._sessions.common.get_server_runner_router",
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
        "agentnexus.server.routes._sessions.common.get_server_runner_router",
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


def test_workspace_merge_operation_store_crud(memory_store: CoordinationStore) -> None:
    from agentnexus.coordination.types import WorkspaceMergeOperation

    op = WorkspaceMergeOperation(
        root_session_id="conv_root_merge",
        holder_session_id="conv_coder_1",
        repo_path=r"U:\worktrees\repo",
        source_branch="feature",
        target_branch="main",
        expected_source_head="deadbeef",
        expected_target_head="cafebabe",
        dirty_hash="aa" * 32,
        fencing_token=7,
        preview={"can_merge": True},
    )
    memory_store.create_merge_operation(op)

    loaded = memory_store.get_merge_operation(op.operation_id)
    assert loaded is not None
    assert loaded.root_session_id == "conv_root_merge"
    assert loaded.preview == {"can_merge": True}
    assert loaded.fencing_token == 7

    updated = memory_store.update_merge_operation(
        op.operation_id, status="merged", result={"merge_head": "a1b2c3d4"}
    )
    assert updated is not None
    assert updated.status == "merged"
    assert updated.result == {"merge_head": "a1b2c3d4"}

    listed = memory_store.list_merge_operations("conv_root_merge")
    assert len(listed) == 1
    assert listed[0].operation_id == op.operation_id


def test_coordination_artifact_metadata_crud(memory_store: CoordinationStore) -> None:
    artifact = CoordinationArtifact(
        root_session_id="conv_root_art",
        producer_session_id="conv_coder_1",
        kind="patch",
        digest="a" * 64,
        uri="artifact://patch.diff",
        metadata={"name": "patch.diff", "lines": 42},
    )
    memory_store.create_artifact(artifact)

    loaded = memory_store.get_artifact(artifact.artifact_id)
    assert loaded is not None
    assert loaded.kind == "patch"
    assert loaded.metadata == {"name": "patch.diff", "lines": 42}

    updated = memory_store.update_artifact_status(artifact.artifact_id, "invalidated")
    assert updated is not None
    assert updated.status == "invalidated"

    listed = memory_store.list_artifacts("conv_root_art")
    assert len(listed) == 1
    assert listed[0].artifact_id == artifact.artifact_id


def test_workspace_coordinator_previews_and_executes_merge(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    repo = init_merge_repo(tmp_path)
    mgr = WorkspaceLeaseManager(memory_store)
    lease = mgr.acquire(repo, "conv_coder_1", mode="write")
    coordinator = WorkspaceCoordinator(mgr)

    op = coordinator.prepare_merge_preview(
        memory_store,
        root_session_id="conv_root_api",
        holder_session_id="conv_coder_1",
        repo_path=repo,
        source_branch="feature",
        target_branch="main",
    )
    assert op.status == "preview"
    assert op.expected_source_head is not None
    assert op.expected_source_head != op.expected_target_head
    assert op.fencing_token == lease.fencing_token
    assert op.preview["can_merge"] is True

    merged = coordinator.execute_merge(
        memory_store,
        operation_id=op.operation_id,
        fencing_token=lease.fencing_token,
    )
    assert merged.status == "merged"
    assert merged.result is not None
    assert merged.result["merge_head"] != merged.expected_target_head

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "main", "-3"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert log.returncode == 0
    assert "feature" in log.stdout


def test_workspace_coordinator_rejects_stale_preview(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    repo = init_merge_repo(tmp_path)
    mgr = WorkspaceLeaseManager(memory_store)
    lease = mgr.acquire(repo, "conv_coder_1", mode="write")
    coordinator = WorkspaceCoordinator(mgr)

    op = coordinator.prepare_merge_preview(
        memory_store,
        root_session_id="conv_root_api",
        holder_session_id="conv_coder_1",
        repo_path=repo,
        source_branch="feature",
        target_branch="main",
    )

    # Advance the source branch after the preview was captured.
    git = subprocess.run(
        ["git", "-C", str(repo), "switch", "feature"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert git.returncode == 0
    (repo / "a.txt").write_text("base\nfeature\nmore\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "more feature"],
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(repo), "switch", "main"],
        capture_output=True,
        text=True,
        check=False,
    )

    with pytest.raises(RuntimeError, match="advanced since preview"):
        coordinator.execute_merge(
            memory_store,
            operation_id=op.operation_id,
            fencing_token=lease.fencing_token,
        )


def test_coordination_message_cancel_queued_only(memory_store: CoordinationStore) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_cancel",
        sender_session_id="conv_planner",
        recipient_session_id="conv_coder",
        intent="task.request",
        payload={"prompt": "cancel me"},
    )
    memory_store.save_message_and_outbox(msg)

    cancelled = memory_store.cancel_message(msg.message_id)
    assert cancelled is not None
    assert cancelled.message_state == "cancelled"
    outbox = memory_store.list_outbox_items(message_id=msg.message_id)[0]
    assert outbox.status == "failed"

    # Idempotent for an already-cancelled message.
    again = memory_store.cancel_message(msg.message_id)
    assert again is not None
    assert again.message_state == "cancelled"

    # An active (already injected) message cannot be cancelled.
    active_msg = AgentMessage(
        root_session_id="conv_root_cancel",
        sender_session_id="conv_planner",
        recipient_session_id="conv_coder",
        intent="task.request",
        payload={"prompt": "already injected"},
    )
    memory_store.save_message_and_outbox(active_msg)
    memory_store.update_message_state(active_msg.message_id, "active")
    remains = memory_store.cancel_message(active_msg.message_id)
    assert remains is not None
    assert remains.message_state == "active"


def test_coordination_message_consumption_receipt_store(
    memory_store: CoordinationStore,
) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_receipt",
        sender_session_id="conv_planner",
        recipient_session_id="conv_coder",
        intent="task.request",
        payload={"prompt": "read me"},
    )
    memory_store.save_message_and_outbox(msg)
    memory_store.update_message_state(msg.message_id, "active")

    updated = memory_store.record_consumption_receipt(
        msg.message_id,
        "acknowledged",
        {"response_id": "resp_1", "mode": "next_turn"},
    )
    assert updated is not None
    assert updated.message_state == "active"
    assert updated.consumption_state == "acknowledged"
    assert updated.consumed_at is not None
    assert updated.consumption_receipt == {
        "response_id": "resp_1",
        "mode": "next_turn",
    }


def test_list_active_unconsumed_for_recipient_filters(
    memory_store: CoordinationStore,
) -> None:
    def _message(message_id: str, recipient: str) -> AgentMessage:
        return AgentMessage(
            message_id=message_id,
            root_session_id="conv_root_unconsumed",
            sender_session_id="conv_planner",
            recipient_session_id=recipient,
            intent="task.request",
            payload={"prompt": f"msg {message_id}"},
        )

    # Delivered but still unconsumed → the receipt cursor must return it.
    pending = _message("msg_pending", "conv_coder")
    memory_store.save_message_and_outbox(pending)
    memory_store.update_message_state(pending.message_id, "active")
    # Delivered and already consumed → must be excluded.
    consumed = _message("msg_consumed", "conv_coder")
    memory_store.save_message_and_outbox(consumed)
    memory_store.update_message_state(consumed.message_id, "active")
    memory_store.record_consumption_receipt(
        consumed.message_id, "consumed", {"source": "turn_completed"}
    )
    # Queued but not yet delivered → must be excluded.
    queued = _message("msg_queued", "conv_coder")
    memory_store.save_message_and_outbox(queued)
    # Addressed to another recipient → must be excluded.
    other = _message("msg_other", "conv_reviewer")
    memory_store.save_message_and_outbox(other)
    memory_store.update_message_state(other.message_id, "active")

    got = memory_store.list_active_unconsumed_for_recipient("conv_coder")
    assert [m.message_id for m in got] == ["msg_pending"]
    assert got[0].consumption_state == "unconsumed"

    memory_store.record_consumption_receipt(
        pending.message_id, "consumed", {"source": "turn_completed"}
    )
    assert memory_store.list_active_unconsumed_for_recipient("conv_coder") == []


def test_coordination_lifecycle_cas_transitions(
    memory_store: CoordinationStore,
) -> None:
    """Run/task transitions are atomic: concurrent writers cannot double-move."""
    run = CoordinationRun(
        run_id="run_cas",
        root_session_id="conv_root_cas",
        template="plan_implement_review_fix_test",
        status="running",
    )
    memory_store.create_run(run)
    task = CoordinationTask(
        run_id=run.run_id,
        title="Stage",
        status="running",
        assignee_session_id="conv_p1",
        assignee_role="planner",
    )
    memory_store.create_task(task)

    assert memory_store.transition_run_status(run.run_id, "paused", from_statuses=["running"])
    assert not memory_store.transition_run_status(
        run.run_id, "paused", from_statuses=["running"]
    )  # idempotent repeat
    with pytest.raises(ValueError, match="cannot transition"):
        memory_store.transition_run_status(run.run_id, "running", from_statuses=["running"])
    assert memory_store.transition_run_status(run.run_id, "running", from_statuses=["paused"])

    assert memory_store.transition_task_status(
        task.task_id, "succeeded", from_statuses=["running"]
    )
    assert not memory_store.transition_task_status(
        task.task_id, "succeeded", from_statuses=["running"]
    )
    with pytest.raises(ValueError, match="cannot transition"):
        memory_store.transition_task_status(task.task_id, "failed", from_statuses=["running"])


@pytest.mark.asyncio
async def test_workflow_duplicate_advance_is_idempotent(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Idempotent Run",
        root_session_id="conv_root_dup",
        planner_session_id="conv_planner_dup",
        implementer_session_id="conv_coder_dup",
        reviewer_session_id="conv_reviewer_dup",
        user_prompt="Idempotent duel",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    plan_task = tasks["planner"]

    first = await engine.advance(run_id=run.run_id, task_id=plan_task.task_id, outcome="succeeded")
    messages_after_first = memory_store.list_messages("conv_root_dup")
    second = await engine.advance(
        run_id=run.run_id, task_id=plan_task.task_id, outcome="succeeded"
    )
    messages_after_second = memory_store.list_messages("conv_root_dup")

    assert first.status == "running"
    assert second.status == "running"
    same_messages = [(m.message_id, m.task_id, m.intent, m.payload) for m in messages_after_first]
    assert same_messages == [
        (m.message_id, m.task_id, m.intent, m.payload) for m in messages_after_second
    ]
    implementer = next(
        m for m in memory_store.list_tasks(run.run_id) if m.assignee_role == "implementer"
    )
    assert implementer.status == "running"


@pytest.mark.asyncio
async def test_workflow_fixed_node_persists_behavior_binding_and_injection(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    await engine.start_plan_implement_review_run(
        title="Behavior Run",
        root_session_id="conv_root_behavior",
        planner_session_id="conv_planner_behavior",
        implementer_session_id="conv_coder_behavior",
        reviewer_session_id="conv_reviewer_behavior",
        user_prompt="Build with lean behavior",
        workspace_path=str(tmp_path),
        behavior_modes={"planner": "lean"},
    )
    kickoff = memory_store.list_messages("conv_root_behavior")[0]
    binding = kickoff.payload["behavior_binding"]

    assert binding["workflow_node"] == "planner"
    assert binding["requested_mode"] == "lean"
    assert binding["injection_channel"] == "composed_per_turn"
    resolved = binding["resolved"]["binding"]
    assert resolved["mode"] == "lean"
    assert resolved["digest"].startswith("sha256:")
    composed = kickoff.payload["prompt"]
    assert "Behavior instructions:" in composed
    assert composed.index("Behavior instructions:") < composed.index(
        "Please analyze and create an implementation plan"
    )
    assert "End your reply with exactly [WORKFLOW_RESULT: succeeded]" in composed


@pytest.mark.asyncio
async def test_dag_workflow_downgrades_unapproved_strict_behavior(
    memory_store: CoordinationStore,
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    await engine.start_dag_workflow_run(
        title="DAG Behavior",
        root_session_id="conv_root_dag_behavior",
        tasks=[
            WorkflowDagTaskSpec(
                name="implement",
                title="Implement",
                assignee_session_id="conv_impl_behavior",
                assignee_role="implementer",
                prompt="Implement the task.",
                behavior_mode="strict",
            )
        ],
    )
    # Root DAG stages are dispatched by the workflow recovery poller, the
    # same path the server lifespan uses through CoordinationWorkflowScheduler.
    assert await engine.reconcile_missing_dispatches() == 1
    message = memory_store.list_messages("conv_root_dag_behavior")[0]
    binding = message.payload["behavior_binding"]

    assert binding["requested_mode"] == "strict"
    assert binding["resolved"]["binding"]["mode"] == "advisory"
    assert "explicit task authorization" in binding["resolved"]["binding"]["reason"]
    assert "Behavior instructions:" in message.payload["prompt"]


@pytest.mark.asyncio
async def test_concurrent_runs_keep_distinct_behavior_modes(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    """Two sessions with different Behavior modes never leak bindings to each other."""
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    await engine.start_plan_implement_review_run(
        title="Run A",
        root_session_id="conv_root_behavior_a",
        planner_session_id="conv_planner_behavior_a",
        implementer_session_id="conv_impl_behavior_a",
        reviewer_session_id="conv_reviewer_behavior_a",
        user_prompt="Lean run",
        workspace_path=str(tmp_path),
        behavior_modes={"planner": "lean"},
    )
    await engine.start_plan_implement_review_run(
        title="Run B",
        root_session_id="conv_root_behavior_b",
        planner_session_id="conv_planner_behavior_b",
        implementer_session_id="conv_impl_behavior_b",
        reviewer_session_id="conv_reviewer_behavior_b",
        user_prompt="Strict run",
        workspace_path=str(tmp_path),
        behavior_modes={"planner": "strict"},
    )

    a = memory_store.list_messages("conv_root_behavior_a")[0].payload["behavior_binding"]
    b = memory_store.list_messages("conv_root_behavior_b")[0].payload["behavior_binding"]
    assert a["requested_mode"] == "lean"
    assert a["resolved"]["binding"]["mode"] == "lean"
    assert b["requested_mode"] == "strict"
    assert b["resolved"]["binding"]["mode"] == "advisory"
    assert a["workflow_node"] == "planner"
    assert b["workflow_node"] == "planner"


@pytest.mark.asyncio
async def test_workflow_pause_resume_lifecycle(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Lifecycle Run",
        root_session_id="conv_root_pause",
        planner_session_id="conv_planner_pause",
        implementer_session_id="conv_coder_pause",
        reviewer_session_id="conv_reviewer_pause",
        user_prompt="Pause me",
        workspace_path=str(tmp_path),
    )
    paused = await engine.pause_run(run.run_id)
    assert paused.status == "paused"
    resumed = await engine.resume_run(run.run_id)
    assert resumed.status == "running"

    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    advanced = await engine.advance(
        run_id=run.run_id, task_id=tasks["planner"].task_id, outcome="succeeded"
    )
    assert advanced.status == "running"


@pytest.mark.asyncio
async def test_workflow_retry_failed_task_resends_stage(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Retry Run",
        root_session_id="conv_root_retry",
        planner_session_id="conv_planner_retry",
        implementer_session_id="conv_coder_retry",
        reviewer_session_id="conv_reviewer_retry",
        user_prompt="Retry me",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    before = len(memory_store.list_messages("conv_root_retry"))

    failed = await engine.advance(
        run_id=run.run_id,
        task_id=tasks["planner"].task_id,
        outcome="failed",
    )
    assert failed.status == "needs_attention"
    assert memory_store.get_task(tasks["planner"].task_id).status == "failed"

    retried = await engine.retry_task(tasks["planner"].task_id)
    assert retried.status == "running"
    assert memory_store.get_task(tasks["planner"].task_id).status == "running"
    after = memory_store.list_messages("conv_root_retry")
    assert len(after) == before + 1
    resent = after[-1]
    assert resent.task_id == tasks["planner"].task_id
    assert resent.recipient_session_id == "conv_planner_retry"
    assert resent.intent == "task.request"

    # A duplicate retry on the now-running task is a no-op: no second resend.
    duplicate = await engine.retry_task(tasks["planner"].task_id)
    assert duplicate.status == "running"
    assert len(memory_store.list_messages("conv_root_retry")) == len(after)


@pytest.mark.asyncio
async def test_workflow_reassign_queued_kickoff_redirects_outbox(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Reassign Queued",
        root_session_id="conv_root_reassign_q",
        planner_session_id="conv_planner_a",
        implementer_session_id="conv_coder_a",
        reviewer_session_id="conv_reviewer_a",
        user_prompt="Reassign queued",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    kickoff = memory_store.list_messages("conv_root_reassign_q")[0]
    assert kickoff.message_state == "queued"

    updated, changed = await engine.reassign_task(tasks["planner"].task_id, "conv_planner_b")
    assert changed is True
    assert updated.status == "running"
    assert memory_store.get_task(tasks["planner"].task_id).assignee_session_id == "conv_planner_b"
    assert memory_store.get_message(kickoff.message_id).recipient_session_id == "conv_planner_b"
    assert (
        memory_store.list_outbox_items(message_id=kickoff.message_id)[0].target_session_id
        == "conv_planner_b"
    )
    events = memory_store.list_events("conv_root_reassign_q")
    assert any(e.event_type == "workflow.task.reassigned" for e in events)

    _, noop = await engine.reassign_task(tasks["planner"].task_id, "conv_planner_b")
    assert noop is False


@pytest.mark.asyncio
async def test_workflow_reassign_active_unconsumed_rejects_and_resends(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Reassign Active",
        root_session_id="conv_root_reassign_a",
        planner_session_id="conv_planner_a",
        implementer_session_id="conv_coder_a",
        reviewer_session_id="conv_reviewer_a",
        user_prompt="Reassign active",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    kickoff = memory_store.list_messages("conv_root_reassign_a")[0]
    memory_store.update_message_state(kickoff.message_id, "active")

    updated, changed = await engine.reassign_task(tasks["planner"].task_id, "conv_planner_b")
    assert changed is True
    assert updated.status == "running"
    old_kickoff = memory_store.get_message(kickoff.message_id)
    assert old_kickoff.consumption_state == "rejected"
    assert old_kickoff.consumption_receipt["source"] == "task_reassigned"

    resent = memory_store.list_messages("conv_root_reassign_a")[-1]
    assert resent.task_id == tasks["planner"].task_id
    assert resent.recipient_session_id == "conv_planner_b"
    assert resent.payload["attempt"] == "reassign"
    assert resent.payload["previous_assignee"] == "conv_planner_a"
    assert resent.correlation_id == f"reassign/{tasks['planner'].task_id}"

    events = memory_store.list_events("conv_root_reassign_a")
    assert any(e.event_type == "message.rejected" for e in events)
    assert any(e.event_type == "message.redirected" for e in events) is False


@pytest.mark.asyncio
async def test_template_upgrade_does_not_mutate_running_run(
    memory_store: CoordinationStore,
) -> None:
    """A newer template instance must not rewrite a running run's snapshot."""
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run_v1 = await engine.start_dag_workflow_run(
        title="Upgrade V1",
        root_session_id="conv_root_upgrade_v1",
        tasks=[
            WorkflowDagTaskSpec(
                name="plan",
                title="V1 Plan",
                assignee_session_id="conv_plan_a",
                assignee_role="planner",
                prompt="Plan v1.",
            ),
            WorkflowDagTaskSpec(
                name="build",
                title="V1 Build",
                assignee_session_id="conv_build_a",
                assignee_role="implementer",
                prompt="Build v1.",
                dependencies=["plan"],
            ),
        ],
    )
    v1_tasks_before = {task.task_id for task in memory_store.list_tasks(run_v1.run_id)}
    v1_template_before = run_v1.template
    v1_metadata_before = dict(run_v1.metadata)

    await engine.start_dag_workflow_run(
        title="Upgrade V2",
        root_session_id="conv_root_upgrade_v2",
        tasks=[
            WorkflowDagTaskSpec(
                name="plan",
                title="V2 Plan",
                assignee_session_id="conv_plan_b",
                assignee_role="planner",
                prompt="Plan v2.",
            ),
            WorkflowDagTaskSpec(
                name="build",
                title="V2 Build",
                assignee_session_id="conv_build_b",
                assignee_role="implementer",
                prompt="Build v2.",
                dependencies=["plan"],
            ),
            WorkflowDagTaskSpec(
                name="ship",
                title="V2 Ship",
                assignee_session_id="conv_ship_b",
                assignee_role="reviewer",
                prompt="Ship v2.",
                dependencies=["build"],
            ),
        ],
    )

    run_v1_after = memory_store.get_run(run_v1.run_id)
    assert run_v1_after is not None
    assert run_v1_after.template == v1_template_before
    assert run_v1_after.metadata == v1_metadata_before
    assert {task.task_id for task in memory_store.list_tasks(run_v1.run_id)} == v1_tasks_before


@pytest.mark.asyncio
async def test_workflow_reassign_rejects_acknowledged_work(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Reassign Ack",
        root_session_id="conv_root_reassign_ack",
        planner_session_id="conv_planner_a",
        implementer_session_id="conv_coder_a",
        reviewer_session_id="conv_reviewer_a",
        user_prompt="Reassign acknowledged",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    kickoff = memory_store.list_messages("conv_root_reassign_ack")[0]
    memory_store.update_message_state(kickoff.message_id, "active")
    memory_store.record_consumption_receipt(
        kickoff.message_id, "acknowledged", {"response_id": "resp_1"}
    )

    with pytest.raises(ValueError, match="already acknowledged"):
        await engine.reassign_task(tasks["planner"].task_id, "conv_planner_b")
    assert memory_store.get_task(tasks["planner"].task_id).assignee_session_id == "conv_planner_a"


@pytest.mark.asyncio
async def test_workflow_retry_budget_blocks_exhausted_retries(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Retry Budget",
        root_session_id="conv_root_retry_budget",
        planner_session_id="conv_planner_budget",
        implementer_session_id="conv_coder_budget",
        reviewer_session_id="conv_reviewer_budget",
        user_prompt="Retry with a budget",
        workspace_path=str(tmp_path),
        budget={"max_retries": 1},
    )
    assert run.budget["max_retries"] == 1
    assert run.metadata["template_version"] == "1.0"
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}

    failed = await engine.advance(
        run_id=run.run_id,
        task_id=tasks["planner"].task_id,
        outcome="failed",
    )
    assert failed.status == "needs_attention"

    retried = await engine.retry_task(tasks["planner"].task_id)
    assert retried.status == "running"
    assert retried.metadata["retry_count"] == 1

    failed_again = await engine.advance(
        run_id=run.run_id,
        task_id=tasks["planner"].task_id,
        outcome="failed",
    )
    assert failed_again.status == "needs_attention"
    with pytest.raises(ValueError, match="retry limit reached"):
        await engine.retry_task(tasks["planner"].task_id)
    events = memory_store.list_events("conv_root_retry_budget")
    assert any(e.event_type == "workflow.retry_limit_reached" for e in events)
    assert memory_store.get_task(tasks["planner"].task_id).status == "failed"


@pytest.mark.asyncio
async def test_workflow_deadline_blocks_further_dispatch(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Deadline Run",
        root_session_id="conv_root_deadline",
        planner_session_id="conv_planner_deadline",
        implementer_session_id="conv_coder_deadline",
        reviewer_session_id="conv_reviewer_deadline",
        user_prompt="Deadline gates dispatch",
        workspace_path=str(tmp_path),
        budget={"deadline_s": time.time() - 10},
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}

    with pytest.raises(ValueError, match="deadline exceeded"):
        await engine.advance(
            run_id=run.run_id,
            task_id=tasks["planner"].task_id,
            outcome="succeeded",
        )
    stored = memory_store.get_run(run.run_id)
    assert stored is not None
    assert stored.status == "needs_attention"
    events = memory_store.list_events("conv_root_deadline")
    assert any(e.event_type == "workflow.deadline_exceeded" for e in events)


@pytest.mark.asyncio
async def test_workflow_reconcile_requeues_missing_dispatch(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Recovery Run",
        root_session_id="conv_root_recover",
        planner_session_id="conv_planner_recover",
        implementer_session_id="conv_coder_recover",
        reviewer_session_id="conv_reviewer_recover",
        user_prompt="Recover the dispatch",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    kickoff = memory_store.list_messages("conv_root_recover")[0]
    cancelled = memory_store.cancel_message(kickoff.message_id)
    assert cancelled is not None
    assert cancelled.message_state == "cancelled"

    scheduler = CoordinationWorkflowScheduler(engine)
    healed = await scheduler.sync_once()
    assert healed == 1

    messages = memory_store.list_messages("conv_root_recover")
    resent = messages[-1]
    assert resent.task_id == tasks["planner"].task_id
    assert resent.recipient_session_id == "conv_planner_recover"
    assert resent.message_state == "queued"
    assert resent.correlation_id == f"recovery/{tasks['planner'].task_id}"
    events = memory_store.list_events("conv_root_recover")
    assert any(e.event_type == "workflow.dispatch.healed" for e in events)

    # A second pass sees the queued request and must not duplicate it.
    again = await scheduler.sync_once()
    assert again == 0
    assert len(memory_store.list_messages("conv_root_recover")) == len(messages)


@pytest.mark.asyncio
async def test_workflow_reconcile_never_replays_consumed_or_paused(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Recovery Skip",
        root_session_id="conv_root_recover_skip",
        planner_session_id="conv_planner_skip",
        implementer_session_id="conv_coder_skip",
        reviewer_session_id="conv_reviewer_skip",
        user_prompt="Do not replay",
        workspace_path=str(tmp_path),
    )
    kickoff = memory_store.list_messages("conv_root_recover_skip")[0]
    memory_store.update_message_state(kickoff.message_id, "active")
    memory_store.record_consumption_receipt(
        kickoff.message_id, "consumed", {"source": "turn_completed"}
    )
    assert await engine.reconcile_missing_dispatches() == 0

    paused = await engine.pause_run(run.run_id)
    assert paused.status == "paused"
    active = memory_store.list_messages("conv_root_recover_skip")[-1]
    memory_store.cancel_message(active.message_id)
    assert await engine.reconcile_missing_dispatches() == 0


@pytest.mark.asyncio
async def test_workflow_reconcile_stops_after_one_failed_heal(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Finite Recovery",
        root_session_id="conv_root_finite",
        planner_session_id="conv_planner_finite",
        implementer_session_id="conv_coder_finite",
        reviewer_session_id="conv_reviewer_finite",
        user_prompt="Do not recover forever",
        workspace_path=str(tmp_path),
    )
    kickoff = memory_store.list_messages(run.root_session_id)[0]
    original_outbox = memory_store.list_outbox_items(message_id=kickoff.message_id)[0]
    for _ in range(5):
        memory_store.requeue_outbox(original_outbox.item_id, next_retry_delay_s=0)

    assert await engine.reconcile_missing_dispatches() == 1
    messages = memory_store.list_messages(run.root_session_id)
    assert len(messages) == 2
    assert all(message.idempotency_key for message in messages)
    healed_outbox = memory_store.list_outbox_items(message_id=messages[-1].message_id)[0]
    for _ in range(5):
        memory_store.requeue_outbox(healed_outbox.item_id, next_retry_delay_s=0)

    assert await engine.reconcile_missing_dispatches() == 0
    stored_run = memory_store.get_run(run.run_id)
    assert stored_run is not None and stored_run.status == "needs_attention"
    planner = next(
        task for task in memory_store.list_tasks(run.run_id) if task.assignee_role == "planner"
    )
    assert planner.status == "failed"
    assert len(memory_store.list_messages(run.root_session_id)) == 2
    events = memory_store.list_events(run.root_session_id)
    assert any(event.event_type == "workflow.dispatch.abandoned" for event in events)


@pytest.mark.asyncio
async def test_workflow_reconcile_cancels_run_with_missing_root(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(
        memory_store,
        WorkspaceCoordinator(),
        conversation_store=FakeConversationStore({}),
    )
    run = await engine.start_plan_implement_review_run(
        title="Orphan Recovery",
        root_session_id="conv_missing_root",
        planner_session_id="conv_missing_planner",
        implementer_session_id="conv_missing_coder",
        reviewer_session_id="conv_missing_reviewer",
        user_prompt="Cancel orphan state",
        workspace_path=str(tmp_path),
    )

    assert await engine.reconcile_missing_dispatches() == 0
    stored = memory_store.get_run(run.run_id)
    assert stored is not None and stored.status == "cancelled"
    assert {task.status for task in memory_store.list_tasks(run.run_id)} == {"cancelled"}
    assert memory_store.list_messages(run.root_session_id)[0].message_state == "cancelled"


@pytest.mark.asyncio
async def test_workflow_cancel_run_marks_messages_and_tasks(
    memory_store: CoordinationStore, tmp_path: Path
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Cancel Run",
        root_session_id="conv_root_cancel",
        planner_session_id="conv_planner_cancel",
        implementer_session_id="conv_coder_cancel",
        reviewer_session_id="conv_reviewer_cancel",
        user_prompt="Cancel me",
        workspace_path=str(tmp_path),
    )
    kickoff = memory_store.list_messages("conv_root_cancel")[0]
    memory_store.update_message_state(kickoff.message_id, "active")

    cancelled = await engine.cancel_run(run.run_id)
    assert cancelled.status == "cancelled"
    for task in memory_store.list_tasks(run.run_id):
        assert task.status == "cancelled"
    assert memory_store.get_message(kickoff.message_id).message_state == "active"
    assert memory_store.get_message(kickoff.message_id).consumption_state == "rejected"
    queued_messages = memory_store.list_messages("conv_root_cancel")[1:]
    assert all(m.message_state == "cancelled" for m in queued_messages)
    events = memory_store.list_events("conv_root_cancel")
    assert any(e.event_type == "workflow.run.cancelled" for e in events)


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

    durable_artifacts = memory_store.list_artifacts(run.root_session_id, run_id=run.run_id)
    assert [a.metadata["name"] for a in durable_artifacts] == [
        "plan.md",
        "patch.diff",
        "test-report.md",
    ]
    assert {a.kind for a in durable_artifacts} == {"plan", "diff", "report"}


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
    workspace_root: Path,
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
        json={
            "run_id": run_id,
            "title": "API Task 1",
            "assignee_role": "implementer",
            "acceptance_criteria": ["Tests pass"],
            "deadline": 1234567890.0,
        },
    )
    assert res_task.status_code == 200
    assert res_task.json()["task"]["title"] == "API Task 1"
    assert res_task.json()["task"]["acceptance_criteria"] == ["Tests pass"]
    assert res_task.json()["task"]["deadline"] == 1234567890.0

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
    assert memory_store.list_messages("conv_root_api")[0].idempotency_key.startswith("auto:")
    duplicate = client.post(
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
    assert duplicate.status_code == 200
    assert len(memory_store.list_messages("conv_root_api")) == 1

    # 4. Acquire Workspace Lease
    res_lease = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root_api",
            "workspace_path": str(workspace_root),
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
    final_run = client.get(f"/v1/coordination/runs/{wf_data['run']['run_id']}").json()["run"]
    assert final_run["status"] == "succeeded"


def test_coordination_api_run_lifecycle_endpoints(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/workflows/plan-implement-review",
        json={
            "title": "Lifecycle API Run",
            "root_session_id": "conv_root_api",
            "planner_session_id": "conv_p1",
            "implementer_session_id": "conv_c1",
            "reviewer_session_id": "conv_r1",
            "user_prompt": "Drive lifecycle API",
        },
    )
    assert res.status_code == 200
    wf = res.json()
    run_id = wf["run"]["run_id"]
    tasks_by_role = {t["assignee_role"]: t["task_id"] for t in wf["tasks"]}

    paused = client.post(f"/v1/coordination/runs/{run_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["run"]["status"] == "paused"
    resumed = client.post(f"/v1/coordination/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["run"]["status"] == "running"

    failed = client.post(
        f"/v1/coordination/workflows/{run_id}/tasks/{tasks_by_role['planner']}/advance",
        json={"outcome": "failed"},
    )
    assert failed.status_code == 200
    assert failed.json()["run"]["status"] == "needs_attention"

    retried = client.post(f"/v1/coordination/tasks/{tasks_by_role['planner']}/retry")
    assert retried.status_code == 200
    assert retried.json()["run"]["status"] == "running"
    assert [t["status"] for t in retried.json()["tasks"] if t["assignee_role"] == "planner"] == [
        "running"
    ]

    cancelled = client.post(f"/v1/coordination/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert all(t["status"] == "cancelled" for t in cancelled.json()["tasks"])

    # Retry after cancel is an idempotent no-op, not a second dispatch.
    rejected = client.post(f"/v1/coordination/tasks/{tasks_by_role['planner']}/retry")
    assert rejected.status_code == 200
    assert rejected.json()["run"]["status"] == "cancelled"


def test_coordination_api_reassign_endpoint(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/workflows/plan-implement-review",
        json={
            "title": "Reassign API Run",
            "root_session_id": "conv_root_api",
            "planner_session_id": "conv_p1",
            "implementer_session_id": "conv_c1",
            "reviewer_session_id": "conv_r1",
            "user_prompt": "Reassign via API",
        },
    )
    assert res.status_code == 200
    wf = res.json()
    planner_id = next(t["task_id"] for t in wf["tasks"] if t["assignee_role"] == "planner")

    cross_tree = client.post(
        f"/v1/coordination/tasks/{planner_id}/reassign",
        json={"assignee_session_id": "other_sender"},
    )
    assert cross_tree.status_code == 403

    reassigned = client.post(
        f"/v1/coordination/tasks/{planner_id}/reassign",
        json={"assignee_session_id": "conv_p2"},
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["reassigned"] is True
    assert (
        next(t for t in reassigned.json()["tasks"] if t["task_id"] == planner_id)[
            "assignee_session_id"
        ]
        == "conv_p2"
    )

    # Acknowledged work cannot be reassigned without cancel/retry first.
    kickoff = memory_store.list_messages("conv_root_api")[0]
    memory_store.update_message_state(kickoff.message_id, "active")
    memory_store.record_consumption_receipt(
        kickoff.message_id, "acknowledged", {"response_id": "resp_api"}
    )
    acked = client.post(
        f"/v1/coordination/tasks/{planner_id}/reassign",
        json={"assignee_session_id": "conv_r1"},
    )
    assert acked.status_code == 409
    assert "already acknowledged" in acked.json()["detail"]


def test_coordination_api_run_summary_and_budget(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/workflows/plan-implement-review",
        json={
            "title": "Summary API Run",
            "root_session_id": "conv_root_api",
            "planner_session_id": "conv_p1",
            "implementer_session_id": "conv_c1",
            "reviewer_session_id": "conv_r1",
            "user_prompt": "Summarize this run",
            "budget": {"max_retries": 2},
        },
    )
    assert res.status_code == 200
    run_id = res.json()["run"]["run_id"]
    planner_id = next(t["task_id"] for t in res.json()["tasks"] if t["assignee_role"] == "planner")

    summary = client.get(f"/v1/coordination/runs/{run_id}/summary")
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    assert payload["run"]["budget"]["max_retries"] == 2
    assert payload["summary"]["template_version"] == "1.0"
    assert payload["summary"]["stage"]["planner"] == "assigned"
    assert payload["summary"]["artifact_count"] == 0

    advanced = client.post(
        f"/v1/coordination/workflows/{run_id}/tasks/{planner_id}/advance",
        json={
            "outcome": "succeeded",
            "artifacts": [{"name": "plan.md", "kind": "plan"}],
        },
    )
    assert advanced.status_code == 200
    summary2 = client.get(f"/v1/coordination/runs/{run_id}/summary")
    assert summary2.status_code == 200
    assert summary2.json()["summary"]["stage"]["planner"] == "succeeded"
    assert summary2.json()["summary"]["stage"]["implementer"] == "running"
    assert summary2.json()["summary"]["artifact_count"] == 1


def test_coordination_api_run_summary_reports_effect_unknown_count(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/runs",
        json={
            "title": "Effect Unknown Summary",
            "root_session_id": "conv_root_api",
            "template": "plan_implement_review",
        },
    )
    assert res.status_code == 200
    run_id = res.json()["run"]["run_id"]

    msg = AgentMessage(
        root_session_id="conv_root_api",
        run_id=run_id,
        sender_session_id="conv_p1",
        recipient_session_id="conv_c1",
        intent="task.request",
        payload={"prompt": "deliver then go silent"},
    )
    memory_store.save_message_and_outbox(msg)
    memory_store.update_message_state(msg.message_id, "active")
    stamped_at = time.time() - 301.0
    memory_store.record_delivery_attempt(
        DeliveryAttempt(
            message_id=msg.message_id,
            target_session_id=msg.recipient_session_id,
            delivery_state="confirmed",
            injection_receipt={"status": "runner_injected"},
            created_at=stamped_at,
            updated_at=stamped_at,
        ),
        mark_outbox_done=True,
    )
    report = reconcile_effect_unknown(memory_store, grace_s=300.0)
    assert report.marked == [msg.message_id]

    summary = client.get(f"/v1/coordination/runs/{run_id}/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["summary"]["effect_unknown_count"] == 1


def test_coordination_api_report_drives_workflow_with_assignee_acl(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/workflows/plan-implement-review",
        json={
            "title": "Report API Run",
            "root_session_id": "conv_root_api",
            "planner_session_id": "conv_p1",
            "implementer_session_id": "conv_c1",
            "reviewer_session_id": "conv_r1",
            "user_prompt": "Drive stages via durable result messages",
        },
    )
    assert res.status_code == 200
    wf = res.json()
    run_id = wf["run"]["run_id"]
    tasks_by_role = {t["assignee_role"]: t["task_id"] for t in wf["tasks"]}

    wrong_actor = client.post(
        f"/v1/coordination/workflows/{run_id}/tasks/{tasks_by_role['planner']}/report",
        json={"actor_session_id": "conv_c1", "outcome": "succeeded"},
    )
    assert wrong_actor.status_code == 403
    assert "not assigned" in wrong_actor.json()["detail"]

    reports = [
        ("planner", "conv_p1", {"outcome": "succeeded"}),
        ("implementer", "conv_c1", {"outcome": "succeeded"}),
        ("reviewer", "conv_r1", {"outcome": "succeeded", "review_decision": "approved"}),
        ("tester", "conv_r1", {"outcome": "succeeded"}),
    ]
    for role, actor, body in reports:
        reported = client.post(
            f"/v1/coordination/workflows/{run_id}/tasks/{tasks_by_role[role]}/report",
            json={"actor_session_id": actor, **body},
        )
        assert reported.status_code == 200, reported.text
        assert reported.json()["message"]["intent"] == "task.result"
        assert reported.json()["message"]["task_id"] == tasks_by_role[role]

    messages = memory_store.list_messages("conv_root_api")
    result_messages = [m for m in messages if m.intent == "task.result"]
    assert len(result_messages) == 4
    final_run = client.get(f"/v1/coordination/runs/{run_id}").json()["run"]
    assert final_run["status"] == "succeeded"
    assert all(
        t["status"] == "succeeded"
        for t in client.get(f"/v1/coordination/runs/{run_id}").json()["tasks"]
    )


@pytest.mark.asyncio
async def test_coordination_report_loop_delivers_each_stage_to_target_runner(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)
    res = client.post(
        "/v1/coordination/workflows/plan-implement-review",
        json={
            "title": "Runner Loop Run",
            "root_session_id": "conv_root_api",
            "planner_session_id": "conv_p1",
            "implementer_session_id": "conv_c1",
            "reviewer_session_id": "conv_r1",
            "user_prompt": "Prove each stage reaches the target runner",
        },
    )
    assert res.status_code == 200
    wf = res.json()
    run_id = wf["run"]["run_id"]
    tasks_by_role = {t["assignee_role"]: t["task_id"] for t in wf["tasks"]}

    router = FakeRunnerRouter(status_code=200)
    monkeypatch.setattr(
        "agentnexus.server.routes._sessions.common.get_server_runner_router",
        lambda: router,
    )
    dispatcher = CoordinationDispatcher(memory_store)

    stages = [
        ("planner", "conv_p1", {"outcome": "succeeded"}),
        ("implementer", "conv_c1", {"outcome": "succeeded"}),
        ("reviewer", "conv_r1", {"outcome": "succeeded", "review_decision": "approved"}),
        ("tester", "conv_r1", {"outcome": "succeeded"}),
    ]
    for index, (role, actor, body) in enumerate(stages):
        count = await dispatcher.dispatch_once()
        expected_count = 1 if index == 0 else 2
        assert count == expected_count
        assert router.called_session_ids[-1] == actor
        reported = client.post(
            f"/v1/coordination/workflows/{run_id}/tasks/{tasks_by_role[role]}/report",
            json={"actor_session_id": actor, **body},
        )
        assert reported.status_code == 200, reported.text

    final_run = client.get(f"/v1/coordination/runs/{run_id}").json()["run"]
    assert final_run["status"] == "succeeded"
    runner_calls = [
        session_id for session_id in router.called_session_ids if session_id != "conv_root_api"
    ]
    assert runner_calls[:4] == [
        "conv_p1",
        "conv_c1",
        "conv_r1",
        "conv_r1",
    ]


def test_coordination_api_deadline_conflict(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/workflows/plan-implement-review",
        json={
            "title": "Deadline API Run",
            "root_session_id": "conv_root_api",
            "planner_session_id": "conv_p1",
            "implementer_session_id": "conv_c1",
            "reviewer_session_id": "conv_r1",
            "user_prompt": "Deadline via API",
            "budget": {"deadline_s": time.time() - 5},
        },
    )
    assert res.status_code == 200
    run_id = res.json()["run"]["run_id"]
    planner_id = next(t["task_id"] for t in res.json()["tasks"] if t["assignee_role"] == "planner")

    blocked = client.post(
        f"/v1/coordination/workflows/{run_id}/tasks/{planner_id}/advance",
        json={"outcome": "succeeded"},
    )
    assert blocked.status_code == 409
    assert "deadline exceeded" in blocked.json()["detail"]
    run = client.get(f"/v1/coordination/runs/{run_id}").json()["run"]
    assert run["status"] == "needs_attention"


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


def test_coordination_api_merge_preview_requires_lease_and_fencing(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
    tmp_path: Path,
) -> None:
    repo = init_merge_repo(tmp_path)
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    # Preview without an active write lease is refused.
    res = client.post(
        "/v1/coordination/workspaces/merge-previews",
        json={
            "root_session_id": "conv_root_api",
            "holder_session_id": "conv_c1",
            "repo_path": str(repo),
            "source_branch": "feature",
            "target_branch": "main",
        },
    )
    assert res.status_code == 409
    assert "write lease required" in res.json()["detail"]

    # Cross-tree holder is rejected before any lease/merge logic runs.
    res = client.post(
        "/v1/coordination/workspaces/merge-previews",
        json={
            "root_session_id": "conv_root_api",
            "holder_session_id": "other_sender",
            "repo_path": str(repo),
            "source_branch": "feature",
            "target_branch": "main",
        },
    )
    assert res.status_code == 403

    lease_res = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root_api",
            "workspace_path": str(repo),
            "holder_session_id": "conv_c1",
            "mode": "write",
        },
    )
    assert lease_res.status_code == 200
    fencing_token = lease_res.json()["lease"]["fencing_token"]

    res_preview = client.post(
        "/v1/coordination/workspaces/merge-previews",
        json={
            "root_session_id": "conv_root_api",
            "holder_session_id": "conv_c1",
            "repo_path": str(repo),
            "source_branch": "feature",
            "target_branch": "main",
        },
    )
    assert res_preview.status_code == 200
    operation = res_preview.json()["operation"]
    assert operation["status"] == "preview"

    res_bad_token = client.post(
        f"/v1/coordination/workspaces/merge-previews/{operation['operation_id']}/execute",
        json={"fencing_token": fencing_token + 1},
    )
    assert res_bad_token.status_code == 409
    assert "token mismatch" in res_bad_token.json()["detail"]

    res_exec = client.post(
        f"/v1/coordination/workspaces/merge-previews/{operation['operation_id']}/execute",
        json={"fencing_token": fencing_token},
    )
    assert res_exec.status_code == 200
    assert res_exec.json()["operation"]["status"] == "merged"

    listed = client.get(
        "/v1/coordination/workspaces/merge-previews",
        params={"root_session_id": "conv_root_api"},
    )
    assert listed.status_code == 200
    assert listed.json()["operations"][0]["operation_id"] == operation["operation_id"]

    fetched = client.get(f"/v1/coordination/workspaces/merge-previews/{operation['operation_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["operation"]["status"] == "merged"


def test_coordination_lease_fails_closed_without_managed_workspace(
    memory_store: CoordinationStore,
    tmp_path: Path,
) -> None:
    conversations = {
        "conv_root": FakeConversation("conv_root", root_conversation_id="conv_root"),
        "conv_holder": FakeConversation("conv_holder", root_conversation_id="conv_root"),
    }
    app = make_api_app(memory_store, conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root",
            "workspace_path": str(tmp_path / "somewhere"),
            "holder_session_id": "conv_holder",
            "mode": "write",
        },
    )
    assert res.status_code == 403
    assert "host-managed session" in res.json()["detail"]


def test_coordination_lease_rejects_relative_and_outside_paths(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
    workspace_root: Path,
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res_rel = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root_api",
            "workspace_path": "relative/path",
            "holder_session_id": "conv_c1",
            "mode": "write",
        },
    )
    assert res_rel.status_code == 400
    assert "absolute path" in res_rel.json()["detail"]

    res_out = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root_api",
            "workspace_path": str(workspace_root.parent / "outside"),
            "holder_session_id": "conv_c1",
            "mode": "write",
        },
    )
    assert res_out.status_code == 403
    assert "outside the managed" in res_out.json()["detail"]


def test_coordination_merge_preview_rejects_outside_boundary(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
    workspace_root: Path,
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/workspaces/merge-previews",
        json={
            "root_session_id": "conv_root_api",
            "holder_session_id": "conv_c1",
            "repo_path": str(workspace_root.parent / "elsewhere"),
            "source_branch": "feature",
            "target_branch": "main",
        },
    )
    assert res.status_code == 403
    assert "outside the managed" in res.json()["detail"]


def test_coordination_worktree_holder_can_lease_root_repo_boundary(
    memory_store: CoordinationStore,
    workspace_root: Path,
) -> None:
    conversations = {
        "conv_root": FakeConversation(
            "conv_root",
            root_conversation_id="conv_root",
            host_id="host_ws_test",
            workspace=str(workspace_root / "repo"),
        ),
        "conv_holder": FakeConversation(
            "conv_holder",
            root_conversation_id="conv_root",
            host_id="host_ws_test",
            workspace=str(workspace_root / "repo-worktrees" / "feature"),
        ),
    }
    app = make_api_app(memory_store, conversations)
    client = TestClient(app)

    lease = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root",
            "workspace_path": str(workspace_root / "repo"),
            "holder_session_id": "conv_holder",
            "mode": "write",
        },
    )
    assert lease.status_code == 200
    assert lease.json()["lease"]["holder_session_id"] == "conv_holder"

    outside = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root",
            "workspace_path": str(workspace_root / "sibling"),
            "holder_session_id": "conv_holder",
            "mode": "write",
        },
    )
    assert outside.status_code == 403
    assert "outside the managed" in outside.json()["detail"]


@pytest.mark.asyncio
async def test_workflow_task_deadline_blocks_advance(
    memory_store: CoordinationStore,
) -> None:
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Task Deadline Run",
        root_session_id="conv_root_deadline",
        planner_session_id="conv_planner_deadline",
        implementer_session_id="conv_coder_deadline",
        reviewer_session_id="conv_reviewer_deadline",
        user_prompt="Build something before the deadline",
        budget={"task_deadline_s": -1},
    )
    tasks = {t.assignee_role: t for t in memory_store.list_tasks(run.run_id)}
    planner = tasks["planner"]
    assert planner.acceptance_criteria
    assert planner.deadline is not None

    with pytest.raises(ValueError, match="deadline exceeded"):
        await engine.advance(
            run_id=run.run_id,
            task_id=planner.task_id,
            outcome="succeeded",
        )

    assert memory_store.get_task(planner.task_id).status == "blocked"
    assert memory_store.get_run(run.run_id).status == "needs_attention"


@pytest.mark.asyncio
async def test_deadline_harvester_reaps_stuck_task(
    memory_store: CoordinationStore,
) -> None:
    """A task stuck active past its deadline is reaped without any in-band
    advance — the gap the background harvester exists to close."""
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Deadline Harvester Run",
        root_session_id="conv_root_harvest",
        planner_session_id="conv_planner_harvest",
        implementer_session_id="conv_coder_harvest",
        reviewer_session_id="conv_reviewer_harvest",
        user_prompt="Build something the harvester will reap",
        budget={"task_deadline_s": -1},
    )
    tasks = memory_store.list_tasks(run.run_id)
    assert tasks and all(t.deadline is not None for t in tasks)

    report = await engine.harvest_expired_task_deadlines_once()
    assert report["expired"] >= 1
    assert report["harvested"] >= 1

    # Every expired task is now blocked and the run surfaced the failure.
    for task in memory_store.list_tasks(run.run_id):
        if task.deadline is not None and task.deadline <= time.time():
            assert task.status == "blocked"
    assert memory_store.get_run(run.run_id).status == "needs_attention"

    events = memory_store.list_events(run.root_session_id)
    harvest_events = [e for e in events if e.event_type == "workflow.task.deadline_exceeded"]
    assert harvest_events and all(e.payload.get("harvester") for e in harvest_events)

    # Idempotent: blocked tasks leave the active status set.
    second = await engine.harvest_expired_task_deadlines_once()
    assert second["harvested"] == 0


@pytest.mark.asyncio
async def test_deadline_harvester_ignores_live_tasks(
    memory_store: CoordinationStore,
) -> None:
    """Tasks with a future deadline are never touched by the harvester."""
    engine = CoordinationWorkflowEngine(memory_store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Deadline Live Run",
        root_session_id="conv_root_harvest_live",
        planner_session_id="conv_planner_harvest_live",
        implementer_session_id="conv_coder_harvest_live",
        reviewer_session_id="conv_reviewer_harvest_live",
        user_prompt="Build something within budget",
        budget={"task_deadline_s": 3600},
    )
    report = await engine.harvest_expired_task_deadlines_once()
    assert report["harvested"] == 0
    assert memory_store.get_run(run.run_id).status != "needs_attention"
    statuses = {t.status for t in memory_store.list_tasks(run.run_id)}
    assert "blocked" not in statuses


def test_coordination_api_artifact_endpoints(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res_cross = client.post(
        "/v1/coordination/artifacts",
        json={
            "root_session_id": "conv_root_api",
            "producer_session_id": "other_sender",
            "kind": "patch",
            "digest": "b" * 64,
        },
    )
    assert res_cross.status_code == 403

    res = client.post(
        "/v1/coordination/artifacts",
        json={
            "root_session_id": "conv_root_api",
            "producer_session_id": "conv_p1",
            "kind": "plan",
            "digest": "c" * 64,
            "uri": "artifact://plan.md",
            "metadata": {"name": "plan.md"},
        },
    )
    assert res.status_code == 200
    artifact_id = res.json()["artifact"]["artifact_id"]

    got = client.get(f"/v1/coordination/artifacts/{artifact_id}")
    assert got.status_code == 200
    assert got.json()["artifact"]["kind"] == "plan"

    listed = client.get(
        "/v1/coordination/artifacts",
        params={"root_session_id": "conv_root_api"},
    )
    assert listed.status_code == 200
    assert len(listed.json()["artifacts"]) == 1

    patched = client.patch(
        f"/v1/coordination/artifacts/{artifact_id}",
        json={"status": "invalidated"},
    )
    assert patched.status_code == 200
    assert patched.json()["artifact"]["status"] == "invalidated"

    missing = client.get("/v1/coordination/artifacts/art_missing")
    assert missing.status_code == 404


def test_coordination_policy_gate_runs_stages_in_order(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    factory = RecordingPolicyGateFactory()
    app.state.coordination_policy_gate = CoordinationPolicyGate(factory)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "conv_p1",
            "recipient_session_id": "conv_c1",
            "run_id": "run_gate_order",
            "intent": "task.request",
            "payload": {"prompt": "run the gate"},
        },
    )

    assert res.status_code == 200, res.text
    assert factory.calls == [
        ("root", "conv_root_api"),
        ("source", "conv_p1"),
        ("target", "conv_c1"),
        ("run", "conv_root_api"),
        ("server_default", "conv_root_api"),
    ]
    messages = memory_store.list_messages("conv_root_api")
    assert messages
    assert messages[0].run_id == "run_gate_order"


def test_coordination_message_deny_is_not_persisted(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    factory = RecordingPolicyGateFactory(
        {
            "source": PolicyResult(
                action=PolicyAction.DENY,
                reason="source session blocks peer routing",
            )
        }
    )
    app.state.coordination_policy_gate = CoordinationPolicyGate(factory)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "conv_p1",
            "recipient_session_id": "conv_c1",
            "intent": "task.request",
            "payload": {"prompt": "must not survive"},
        },
    )

    assert res.status_code == 403
    assert "source session blocks peer routing" in res.json()["detail"]
    assert memory_store.list_messages("conv_root_api") == []
    events = memory_store.list_events("conv_root_api")
    assert any(e.event_type == "policy.deny.coordination_message" for e in events)
    assert any(e.event_type == "message.task.request" for e in events) is False


def test_coordination_message_ask_requires_manage_acl(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    factory = RecordingPolicyGateFactory(
        {
            "target": PolicyResult(
                action=PolicyAction.ASK,
                reason="target approval required",
            )
        }
    )
    app.state.coordination_policy_gate = CoordinationPolicyGate(factory)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "conv_p1",
            "recipient_session_id": "conv_c1",
            "intent": "task.request",
            "payload": {"prompt": "ask gate"},
        },
    )

    assert res.status_code == 403
    assert "manage" in res.json()["detail"]
    assert memory_store.list_messages("conv_root_api") == []


def test_coordination_workspace_and_merge_gates_fail_closed(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
    workspace_root: Path,
) -> None:
    app = make_api_app(memory_store, default_conversations)
    factory = RecordingPolicyGateFactory(
        {
            "server_default": PolicyResult(
                action=PolicyAction.DENY,
                reason="default policy blocks workspace ops",
            )
        }
    )
    app.state.coordination_policy_gate = CoordinationPolicyGate(factory)
    client = TestClient(app)

    lease = client.post(
        "/v1/coordination/workspaces/lease",
        json={
            "root_session_id": "conv_root_api",
            "workspace_path": str(workspace_root),
            "holder_session_id": "conv_c1",
            "mode": "write",
        },
    )
    assert lease.status_code == 403

    preview = client.post(
        "/v1/coordination/workspaces/merge-previews",
        json={
            "root_session_id": "conv_root_api",
            "holder_session_id": "conv_c1",
            "repo_path": str(workspace_root / "repo_does_not_exist"),
            "source_branch": "feature",
            "target_branch": "main",
        },
    )
    assert preview.status_code == 403


def test_coordination_send_message_rejects_over_limit_envelopes(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)
    base = {
        "root_session_id": "conv_root_api",
        "sender_session_id": "conv_p1",
        "recipient_session_id": "conv_c1",
        "intent": "task.request",
        "payload": {"prompt": "ok"},
    }

    too_many_hops = client.post("/v1/coordination/messages", json={**base, "max_hops": 9})
    assert too_many_hops.status_code == 400

    exhausted_hop = client.post(
        "/v1/coordination/messages",
        json={**base, "hop_count": 8, "max_hops": 8},
    )
    assert exhausted_hop.status_code == 400

    bad_ttl = client.post("/v1/coordination/messages", json={**base, "ttl_seconds": 0})
    assert bad_ttl.status_code == 400

    oversized = client.post(
        "/v1/coordination/messages",
        json={**base, "payload": {"blob": "x" * (DEFAULT_MAX_PAYLOAD_BYTES + 1)}},
    )
    assert oversized.status_code == 413
    assert "artifact" in oversized.json()["detail"]
    assert memory_store.list_messages("conv_root_api") == []


@pytest.mark.asyncio
async def test_dispatcher_expires_ttl_message_before_delivery(
    memory_store: CoordinationStore,
) -> None:
    msg = AgentMessage(
        root_session_id="conv_root_ttl",
        sender_session_id="conv_planner_ttl",
        recipient_session_id="conv_coder_ttl",
        intent="task.request",
        payload={"prompt": "expire me"},
        ttl_seconds=1,
        created_at=time.time() - 100,
    )
    memory_store.save_message_and_outbox(msg)

    dispatcher = CoordinationDispatcher(memory_store)
    count = await dispatcher.dispatch_once()
    assert count == 1

    stored = memory_store.get_message(msg.message_id)
    assert stored is not None
    assert stored.message_state == "expired"
    outbox = memory_store.list_outbox_items(message_id=msg.message_id)[0]
    assert outbox.status == "failed"
    attempts = memory_store.list_delivery_attempts(msg.message_id)
    assert attempts[0].delivery_state == "failed"
    assert "TTL" in attempts[0].error
    assert any(
        event.event_type == "message.expired"
        for event in memory_store.list_events("conv_root_ttl")
    )


def test_coordination_api_message_cancel(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "conv_p1",
            "recipient_session_id": "conv_c1",
            "sender_role": "planner",
            "intent": "task.request",
            "payload": {"prompt": "send then cancel"},
        },
    )
    assert res.status_code == 200
    message_id = res.json()["message"]["message_id"]

    cancelled = client.post(f"/v1/coordination/messages/{message_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    assert cancelled.json()["message"]["message_state"] == "cancelled"

    # Cancelling again is idempotent.
    again = client.post(f"/v1/coordination/messages/{message_id}/cancel")
    assert again.status_code == 200

    # An injected/active message is refused.
    active_msg, _ = memory_store.save_message_and_outbox(
        AgentMessage(
            root_session_id="conv_root_api",
            sender_session_id="conv_p1",
            recipient_session_id="conv_c1",
            intent="task.request",
            payload={"prompt": "already active"},
        )
    )
    memory_store.update_message_state(active_msg.message_id, "active")
    active_res = client.post(f"/v1/coordination/messages/{active_msg.message_id}/cancel")
    assert active_res.status_code == 409
    assert "only queued messages" in active_res.json()["detail"]


def test_coordination_api_message_receipt(
    memory_store: CoordinationStore,
    default_conversations: dict[str, FakeConversation],
) -> None:
    app = make_api_app(memory_store, default_conversations)
    client = TestClient(app)

    res = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": "conv_root_api",
            "sender_session_id": "conv_p1",
            "recipient_session_id": "conv_c1",
            "sender_role": "planner",
            "intent": "task.request",
            "payload": {"prompt": "consume me"},
        },
    )
    assert res.status_code == 200
    message_id = res.json()["message"]["message_id"]

    wrong_actor = client.post(
        f"/v1/coordination/messages/{message_id}/receipt",
        json={
            "state": "consumed",
            "actor_session_id": "conv_p1",
            "receipt": {"source": "sender"},
        },
    )
    assert wrong_actor.status_code == 403

    queued = client.post(
        f"/v1/coordination/messages/{message_id}/receipt",
        json={
            "state": "consumed",
            "actor_session_id": "conv_c1",
            "receipt": {"source": "target"},
        },
    )
    assert queued.status_code == 409
    assert "actively delivered" in queued.json()["detail"]

    memory_store.update_message_state(message_id, "active")
    receipt = client.post(
        f"/v1/coordination/messages/{message_id}/receipt",
        json={
            "state": "acknowledged",
            "actor_session_id": "conv_c1",
            "receipt": {"response_id": "resp_abc"},
        },
    )
    assert receipt.status_code == 200
    assert receipt.json()["message"]["consumption_state"] == "acknowledged"
    assert receipt.json()["message"]["consumption_receipt"] == {"response_id": "resp_abc"}

    # Re-reporting the same state is idempotent.
    again = client.post(
        f"/v1/coordination/messages/{message_id}/receipt",
        json={
            "state": "acknowledged",
            "actor_session_id": "conv_c1",
            "receipt": {"response_id": "resp_abc"},
        },
    )
    assert again.status_code == 200

    events = client.get(
        "/v1/coordination/events",
        params={"root_session_id": "conv_root_api"},
    ).json()["events"]
    assert any(e["event_type"] == "message.acknowledged" for e in events)
