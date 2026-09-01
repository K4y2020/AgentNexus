"""Server-lifespan A2A loop tests for the AgentNexus control plane.

These tests drive the real ``create_app`` FastAPI app (and therefore the
real lifespan, outbox poller, workflow scheduler and terminal-idle bridge)
rather than constructing a bare coordination router. A fake RunnerRouter
stands in for the runner HTTP hop, matching the plan's integration tier:
Server ↔ Router ↔ receipt without paid model calls.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.entities.conversation import MessageData, NewConversationItem
from omnigent.server import session_live_state


def _wait_until(predicate: Any, *, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met before timeout")


class _RunnerResponse:
    status_code: int
    text: str

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "{}"


class _RunnerClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.posted: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any], **_: object) -> _RunnerResponse:
        self.posted.append({"url": url, "json": json})
        return _RunnerResponse(self.status_code)


class _RoutedRunner:
    def __init__(self, client: _RunnerClient) -> None:
        self.runner_id = "runner_e2e"
        self.client = client


class _RunnerRouter:
    def __init__(self, status_code: int = 200) -> None:
        self.client = _RunnerClient(status_code)
        self.called_session_ids: list[str] = []

    def client_for_session_resources(self, session_id: str) -> _RoutedRunner:
        self.called_session_ids.append(session_id)
        return _RoutedRunner(self.client)


def _seed_coordination_tree(app: FastAPI) -> tuple[object, object, object, object]:
    store = app.state.conversation_store
    root = store.create_conversation(title="E2E root")
    planner = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="planner:e2e",
    )
    implementer = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="implementer:e2e",
    )
    reviewer = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="reviewer:e2e",
    )
    return root, planner, implementer, reviewer


def test_server_lifespan_dispatch_receipt_and_workflow_advance(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifespan dispatcher delivers, receipts and auto-advances one full stage."""
    root, planner, implementer, reviewer = _seed_coordination_tree(app)
    coordination_store = app.state.coordination_store
    assert coordination_store is not None

    fake_router = _RunnerRouter()
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: fake_router,
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/coordination/workflows/plan-implement-review",
                json={
                    "title": "Server Loop",
                    "root_session_id": root.id,
                    "planner_session_id": planner.id,
                    "implementer_session_id": implementer.id,
                    "reviewer_session_id": reviewer.id,
                    "user_prompt": "Build and review it",
                    "workspace_path": ".",
                },
            )
            assert resp.status_code == 200, resp.text
            run_id = resp.json()["run"]["run_id"]

            _wait_until(
                lambda: any(
                    message.recipient_session_id == planner.id
                    and message.message_state == "active"
                    for message in coordination_store.list_messages(root.id)
                )
            )
            assert planner.id in fake_router.called_session_ids
            kickoff = next(
                message
                for message in coordination_store.list_messages(root.id)
                if message.recipient_session_id == planner.id
            )
            assert kickoff.intent == "task.request"
            assert any(
                post["json"]["metadata"].get("a2a") is True
                for post in fake_router.client.posted
            )

            app.state.conversation_store.append(
                planner.id,
                [
                    NewConversationItem(
                        type="message",
                        response_id="resp_plan_e2e",
                        data=MessageData(
                            role="assistant",
                            content=[
                                {
                                    "type": "output_text",
                                    "text": "Plan ready.\n[WORKFLOW_RESULT: succeeded]",
                                }
                            ],
                            agent="planner",
                        ),
                    )
                ],
            )
            session_live_state.persist_a2a_turn_completed(planner.id, "resp_plan_e2e")

            _wait_until(
                lambda: any(
                    task.assignee_role == "implementer"
                    and task.status == "running"
                    for task in coordination_store.list_tasks(run_id)
                )
            )
            _wait_until(
                lambda: any(
                    message.recipient_session_id == implementer.id
                    and message.message_state == "active"
                    for message in coordination_store.list_messages(root.id)
                )
            )

            latest_kickoff = coordination_store.get_message(kickoff.message_id)
            assert latest_kickoff is not None
            assert latest_kickoff.consumption_state == "consumed"
            assert implementer.id in fake_router.called_session_ids
            assert any(
                post["json"]["metadata"].get("intent") == "task.request"
                and post["url"].endswith(f"/sessions/{implementer.id}/events")
                for post in fake_router.client.posted
            )
    finally:
        session_live_state.configure(None)


def test_server_lifespan_runner_rejection_stays_unconfirmed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runner 503 must not confirm the outbox item or flip the message active."""
    root, planner, _implementer, _reviewer = _seed_coordination_tree(app)
    coordination_store = app.state.coordination_store
    assert coordination_store is not None

    fake_router = _RunnerRouter(status_code=503)
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: fake_router,
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/coordination/messages",
                json={
                    "root_session_id": root.id,
                    "sender_session_id": root.id,
                    "sender_role": "user_orchestrator",
                    "recipient_session_id": planner.id,
                    "recipient_role": "planner",
                    "intent": "task.request",
                    "payload": {"prompt": "Plan this"},
                },
            )
            assert resp.status_code == 200, resp.text
            message_id = resp.json()["message"]["message_id"]

            def _retried() -> bool:
                item_rows = coordination_store.list_outbox_items(message_id=message_id)
                return any(item.retry_count >= 1 for item in item_rows)

            _wait_until(_retried)
            message = coordination_store.get_message(message_id)
            assert message is not None
            assert message.message_state == "queued"
            attempts = coordination_store.list_delivery_attempts(message_id)
            assert attempts and all(
                attempt.delivery_state == "failed" for attempt in attempts
            )
            item_rows = coordination_store.list_outbox_items(message_id=message_id)
            assert item_rows and all(item.status == "pending" for item in item_rows)
            assert len(fake_router.client.posted) >= 1
    finally:
        session_live_state.configure(None)


def test_server_lifespan_template_dag_dispatches_on_dependencies(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A template DAG forks, joins and only dispatches ready stages."""
    root, _planner, _implementer, _reviewer = _seed_coordination_tree(app)
    store = app.state.coordination_store
    assert store is not None
    conversation_store = app.state.conversation_store

    analysis = conversation_store.create_conversation(
        parent_conversation_id=root.id, kind="sub_agent"
    )
    diag = conversation_store.create_conversation(
        parent_conversation_id=root.id, kind="sub_agent"
    )
    code = conversation_store.create_conversation(
        parent_conversation_id=root.id, kind="sub_agent"
    )
    cleanup = conversation_store.create_conversation(
        parent_conversation_id=root.id, kind="sub_agent"
    )

    fake_router = _RunnerRouter()
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: fake_router,
    )
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/coordination/workflows/template",
                json={
                    "title": "Fork Join DAG",
                    "root_session_id": root.id,
                    "tasks": [
                        {
                            "name": "analysis",
                            "title": "Analyze",
                            "assignee_session_id": analysis.id,
                            "assignee_role": "analyst",
                            "prompt": "Analyze the problem.",
                        },
                        {
                            "name": "diag",
                            "title": "Diagnose",
                            "assignee_session_id": diag.id,
                            "assignee_role": "diagnoser",
                            "prompt": "Diagnose the issue.",
                            "dependencies": ["analysis"],
                        },
                        {
                            "name": "code",
                            "title": "Implement",
                            "assignee_session_id": code.id,
                            "assignee_role": "coder",
                            "prompt": "Implement the fix.",
                            "dependencies": ["analysis"],
                        },
                        {
                            "name": "cleanup",
                            "title": "Verify & cleanup",
                            "assignee_session_id": cleanup.id,
                            "assignee_role": "validator",
                            "prompt": "Run verification and cleanup.",
                            "dependencies": ["diag", "code"],
                        },
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            run_id = body["run"]["run_id"]
            assert len(body["tasks"]) == 4
            by_session = {
                task["assignee_session_id"]: task["status"] for task in body["tasks"]
            }
            assert by_session[analysis.id] == "assigned"
            assert by_session[diag.id] == "queued"
            assert by_session[code.id] == "queued"
            assert by_session[cleanup.id] == "queued"
            _wait_until(
                lambda: any(
                    msg.recipient_session_id == analysis.id
                    and msg.message_state == "active"
                    for msg in store.list_messages(root.id)
                )
            )

            analysis_msg = next(
                msg
                for msg in store.list_messages(root.id)
                if msg.recipient_session_id == analysis.id
            )
            app.state.conversation_store.append(
                analysis.id,
                [
                    NewConversationItem(
                        type="message",
                        response_id="resp_dag_analysis",
                        data=MessageData(
                            role="assistant",
                            content=[
                                {
                                    "type": "output_text",
                                    "text": "Done.\n[WORKFLOW_RESULT: succeeded]",
                                }
                            ],
                            agent="analyst",
                        ),
                    )
                ],
            )
            session_live_state.persist_a2a_turn_completed(analysis.id, "resp_dag_analysis")
            def _analysis_consumed() -> bool:
                current = store.get_message(analysis_msg.message_id)
                return current is not None and current.consumption_state == "consumed"

            _wait_until(_analysis_consumed)

            def _forked() -> bool:
                current = {t.assignee_session_id: t.status for t in store.list_tasks(run_id)}
                return (
                    current.get(diag.id) == "running"
                    and current.get(code.id) == "running"
                    and current.get(cleanup.id) == "queued"
                )

            _wait_until(_forked)
            diag_task = next(
                task
                for task in store.list_tasks(run_id)
                if task.assignee_session_id == diag.id
            )
            code_task = next(
                task
                for task in store.list_tasks(run_id)
                if task.assignee_session_id == code.id
            )
            for session_id, task_id in (
                (diag.id, diag_task.task_id),
                (code.id, code_task.task_id),
            ):
                report = client.post(
                    f"/v1/coordination/workflows/{run_id}/tasks/{task_id}/report",
                    json={
                        "actor_session_id": session_id,
                        "outcome": "succeeded",
                    },
                )
                assert report.status_code == 200, report.text

            def _joined() -> bool:
                cleanup_task = next(
                    task
                    for task in store.list_tasks(run_id)
                    if task.assignee_session_id == cleanup.id
                )
                return cleanup_task.status == "running"

            _wait_until(_joined)
    finally:
        session_live_state.configure(None)


def test_server_lifespan_template_dag_failure_blocks_dependents(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed DAG stage moves the run to needs_attention, not downstream."""
    root, _planner, _implementer, _reviewer = _seed_coordination_tree(app)
    store = app.state.coordination_store
    assert store is not None
    conversation_store = app.state.conversation_store
    first = conversation_store.create_conversation(
        parent_conversation_id=root.id, kind="sub_agent"
    )
    second = conversation_store.create_conversation(
        parent_conversation_id=root.id, kind="sub_agent"
    )
    fake_router = _RunnerRouter()
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: fake_router,
    )
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/v1/coordination/workflows/template",
                json={
                    "title": "Fail Stop DAG",
                    "root_session_id": root.id,
                    "tasks": [
                        {
                            "name": "one",
                            "title": "One",
                            "assignee_session_id": first.id,
                            "assignee_role": "worker",
                            "prompt": "Do one.",
                        },
                        {
                            "name": "two",
                            "title": "Two",
                            "assignee_session_id": second.id,
                            "assignee_role": "worker",
                            "prompt": "Do two.",
                            "dependencies": ["one"],
                        },
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            run_id = resp.json()["run"]["run_id"]
            first_task = next(
                task
                for task in store.list_tasks(run_id)
                if task.assignee_session_id == first.id
            )
            report = client.post(
                f"/v1/coordination/workflows/{run_id}/tasks/{first_task.task_id}/report",
                json={
                    "actor_session_id": first.id,
                    "outcome": "failed",
                    "summary": "could not proceed",
                },
            )
            assert report.status_code == 200, report.text
            assert store.get_run(run_id).status == "needs_attention"
            second_task = next(
                task
                for task in store.list_tasks(run_id)
                if task.assignee_session_id == second.id
            )
            assert second_task.status == "queued"
            assert not any(
                msg.recipient_session_id == second.id
                for msg in store.list_messages(root.id)
            )
    finally:
        session_live_state.configure(None)


def test_template_workflow_rejects_cycles(app: FastAPI) -> None:
    """Dependency cycles fail closed before any run/task is created."""
    root, _planner, _implementer, _reviewer = _seed_coordination_tree(app)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/coordination/workflows/template",
            json={
                "title": "Cycle DAG",
                "root_session_id": root.id,
                "tasks": [
                    {
                        "name": "a",
                        "title": "A",
                        "assignee_session_id": root.id,
                        "assignee_role": "worker",
                        "prompt": "A.",
                        "dependencies": ["b"],
                    },
                    {
                        "name": "b",
                        "title": "B",
                        "assignee_session_id": root.id,
                        "assignee_role": "worker",
                        "prompt": "B.",
                        "dependencies": ["a"],
                    },
                ],
            },
        )
        assert resp.status_code == 400
        assert "cycle" in resp.json()["detail"]
