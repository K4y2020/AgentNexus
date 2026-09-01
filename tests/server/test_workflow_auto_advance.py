"""Tests for terminal-driven automatic workflow advancement.

``persist_a2a_turn_completed`` can already prove a turn ended; these tests
cover the additional contract that it only advances a workflow stage when the
latest assistant message declares a controlled result marker. Missing markers
keep the manual report/retry gate, and failed markers route to
``needs_attention``.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.workflow_auto_advance import (
    WorkflowAutoAdvancer,
    assistant_text_from_items,
    declared_outcome,
)
from omnigent.coordination.workflow_engine import CoordinationWorkflowEngine
from omnigent.entities.conversation import ConversationItem, MessageData
from omnigent.server import session_live_state
from omnigent.workspaces.lease import WorkspaceCoordinator


def _wait_until(predicate, *, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)


def _assistant_item(text: str, response_id: str = "resp_auto") -> ConversationItem:
    return ConversationItem(
        id="msg_latest",
        type="message",
        status="completed",
        response_id=response_id,
        created_at=1,
        data=MessageData(
            role="assistant",
            content=[{"type": "output_text", "text": text}],
            agent="planner",
        ),
    )


class _FakeConversationStore:
    def __init__(self, item: ConversationItem | None) -> None:
        self.item = item

    def list_items(
        self,
        conversation_id: str,
        limit: int = 100,
        after: str | None = None,
        before: str | None = None,
        order: str = "asc",
        type: str | None = None,
    ):
        del conversation_id, limit, after, before, order, type
        return SimpleNamespace(data=[self.item] if self.item is not None else [])


def test_declared_outcome_parses_controlled_markers() -> None:
    assert declared_outcome(
        "Plan is ready.\n[WORKFLOW_RESULT: succeeded]",
        "planner",
        "task.request",
    ) == ("succeeded", None)
    assert declared_outcome(
        "[REVIEW_DECISION: changes_requested]",
        "reviewer",
        "review.request",
    ) == ("succeeded", "changes_requested")
    assert declared_outcome(
        "No marker here.",
        "implementer",
        "task.request",
    ) == (None, None)


def test_assistant_text_scopes_to_response_and_skips_non_text() -> None:
    item = _assistant_item("first", response_id="resp_old")
    latest = _assistant_item("second", response_id="resp_new")
    page = SimpleNamespace(data=[item, latest])
    assert assistant_text_from_items(page, "resp_new") == "second"


@pytest.mark.asyncio
async def test_terminal_idle_advances_planner_with_declared_result(
    tmp_path: object,
) -> None:
    store = CoordinationStore(tmp_path / "auto_advance.db")
    engine = CoordinationWorkflowEngine(store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Auto Advance",
        root_session_id="conv_root_auto",
        planner_session_id="conv_planner_auto",
        implementer_session_id="conv_coder_auto",
        reviewer_session_id="conv_reviewer_auto",
        user_prompt="Auto advance me",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in store.list_tasks(run.run_id)}
    kickoff = store.list_messages(run.root_session_id)[0]
    store.update_message_state(kickoff.message_id, "active")

    fake = _FakeConversationStore(
        _assistant_item("Plan ready.\n[WORKFLOW_RESULT: succeeded]", "resp_auto")
    )
    advancer = WorkflowAutoAdvancer(engine, fake)  # type: ignore[arg-type]
    session_live_state.configure(fake, None, store, advancer)  # type: ignore[arg-type]
    try:
        session_live_state.persist_a2a_turn_completed("conv_planner_auto", "resp_auto")
        _wait_until(
            lambda: store.get_task(tasks["planner"].task_id).status == "succeeded"
        )
    finally:
        session_live_state.configure(None)

    assert store.get_message(kickoff.message_id).consumption_state == "consumed"
    assert store.get_task(tasks["implementer"].task_id).status == "running"
    assert any(
        message.recipient_session_id == "conv_coder_auto"
        for message in store.list_messages(run.root_session_id)
    )


@pytest.mark.asyncio
async def test_terminal_idle_without_marker_keeps_manual_gate(
    tmp_path: object,
) -> None:
    store = CoordinationStore(tmp_path / "auto_noop.db")
    engine = CoordinationWorkflowEngine(store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Manual Gate",
        root_session_id="conv_root_manual",
        planner_session_id="conv_planner_manual",
        implementer_session_id="conv_coder_manual",
        reviewer_session_id="conv_reviewer_manual",
        user_prompt="Keep me manual",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in store.list_tasks(run.run_id)}
    kickoff = store.list_messages(run.root_session_id)[0]
    store.update_message_state(kickoff.message_id, "active")

    fake = _FakeConversationStore(_assistant_item("Plan complete."))
    advancer = WorkflowAutoAdvancer(engine, fake)  # type: ignore[arg-type]
    session_live_state.configure(fake, None, store, advancer)  # type: ignore[arg-type]
    try:
        session_live_state.persist_a2a_turn_completed("conv_planner_manual")
        _wait_until(
            lambda: store.get_message(kickoff.message_id).consumption_state == "consumed"
        )
    finally:
        session_live_state.configure(None)

    planner = store.get_task(tasks["planner"].task_id)
    assert planner.status in ("assigned", "running")
    assert len(store.list_messages(run.root_session_id)) == 1


@pytest.mark.asyncio
async def test_terminal_idle_failed_marker_halts_run(tmp_path: object) -> None:
    store = CoordinationStore(tmp_path / "auto_fail.db")
    engine = CoordinationWorkflowEngine(store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Fail Fast",
        root_session_id="conv_root_fail",
        planner_session_id="conv_planner_fail",
        implementer_session_id="conv_coder_fail",
        reviewer_session_id="conv_reviewer_fail",
        user_prompt="Fail me",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in store.list_tasks(run.run_id)}
    kickoff = store.list_messages(run.root_session_id)[0]
    store.update_message_state(kickoff.message_id, "active")

    fake = _FakeConversationStore(
        _assistant_item("[WORKFLOW_RESULT: failed]", "resp_fail")
    )
    advancer = WorkflowAutoAdvancer(engine, fake)  # type: ignore[arg-type]
    session_live_state.configure(fake, None, store, advancer)  # type: ignore[arg-type]
    try:
        session_live_state.persist_a2a_turn_completed("conv_planner_fail", "resp_fail")
        _wait_until(
            lambda: store.get_task(tasks["planner"].task_id).status == "failed"
        )
    finally:
        session_live_state.configure(None)

    assert store.get_run(run.run_id).status == "needs_attention"


@pytest.mark.asyncio
async def test_workflow_auto_advances_full_fix_loop(tmp_path: object) -> None:
    store = CoordinationStore(tmp_path / "auto_full.db")
    engine = CoordinationWorkflowEngine(store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Full Auto Loop",
        root_session_id="conv_root_full",
        planner_session_id="conv_planner_full",
        implementer_session_id="conv_coder_full",
        reviewer_session_id="conv_reviewer_full",
        user_prompt="Build and review it",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in store.list_tasks(run.run_id)}

    fake = _FakeConversationStore(None)
    advancer = WorkflowAutoAdvancer(engine, fake)  # type: ignore[arg-type]
    session_live_state.configure(fake, None, store, advancer)  # type: ignore[arg-type]

    def _active_for_recipient(recipient: str):
        message = next(
            message
            for message in store.list_messages(run.root_session_id)
            if message.recipient_session_id == recipient
            and message.message_state == "queued"
            and message.consumption_state == "unconsumed"
        )
        store.update_message_state(message.message_id, "active")
        return message

    try:
        fake.item = _assistant_item("Plan ready.\n[WORKFLOW_RESULT: succeeded]", "resp_plan")
        _active_for_recipient("conv_planner_full")
        session_live_state.persist_a2a_turn_completed("conv_planner_full", "resp_plan")
        _wait_until(
            lambda: store.get_task(tasks["implementer"].task_id).status == "running"
        )

        fake.item = _assistant_item(
            "Implemented.\n[WORKFLOW_RESULT: succeeded]", "resp_impl"
        )
        _active_for_recipient("conv_coder_full")
        session_live_state.persist_a2a_turn_completed("conv_coder_full", "resp_impl")
        _wait_until(
            lambda: store.get_task(tasks["reviewer"].task_id).status == "running"
        )

        fake.item = _assistant_item(
            "Needs cleanup.\n[REVIEW_DECISION: changes_requested]", "resp_review"
        )
        _active_for_recipient("conv_reviewer_full")
        session_live_state.persist_a2a_turn_completed("conv_reviewer_full", "resp_review")
        _wait_until(
            lambda: store.get_task(tasks["fixer"].task_id).status == "running"
        )
        assert store.get_task(tasks["reviewer"].task_id).status == "waiting_review"

        fake.item = _assistant_item("Fixed.\n[WORKFLOW_RESULT: succeeded]", "resp_fix")
        _active_for_recipient("conv_coder_full")
        session_live_state.persist_a2a_turn_completed("conv_coder_full", "resp_fix")
        _wait_until(
            lambda: store.get_task(tasks["reviewer"].task_id).status == "running"
        )

        fake.item = _assistant_item(
            "Approved.\n[REVIEW_DECISION: approved]", "resp_rereview"
        )
        _active_for_recipient("conv_reviewer_full")
        session_live_state.persist_a2a_turn_completed("conv_reviewer_full", "resp_rereview")
        _wait_until(
            lambda: store.get_task(tasks["tester"].task_id).status == "running"
        )

        fake.item = _assistant_item("All green.\n[WORKFLOW_RESULT: succeeded]", "resp_test")
        _active_for_recipient("conv_reviewer_full")
        session_live_state.persist_a2a_turn_completed("conv_reviewer_full", "resp_test")
        _wait_until(lambda: store.get_run(run.run_id).status == "succeeded")
    finally:
        session_live_state.configure(None)

    assert store.get_task(tasks["planner"].task_id).status == "succeeded"
    assert store.get_task(tasks["implementer"].task_id).status == "succeeded"
    assert store.get_task(tasks["reviewer"].task_id).status == "succeeded"
    assert store.get_task(tasks["fixer"].task_id).status == "succeeded"
    assert store.get_task(tasks["tester"].task_id).status == "succeeded"


class _DispatchRunnerResponse:
    status_code = 200
    text = "{}"


class _DispatchRunnerClient:
    def __init__(self) -> None:
        self.posted: list[dict[str, object]] = []

    async def post(self, url: str, json: dict[str, object], **_: object):
        self.posted.append({"url": url, "json": json})
        return _DispatchRunnerResponse()


class _DispatchRoutedRunner:
    def __init__(self, client: _DispatchRunnerClient) -> None:
        self.runner_id = "runner_dispatch"
        self.client = client


class _DispatchRunnerRouter:
    def __init__(self) -> None:
        self.client = _DispatchRunnerClient()
        self.called_session_ids: list[str] = []

    def client_for_session_resources(self, session_id: str) -> _DispatchRoutedRunner:
        self.called_session_ids.append(session_id)
        return _DispatchRoutedRunner(self.client)


@pytest.mark.asyncio
async def test_dispatched_workflow_message_auto_advances_on_turn_completed(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnigent.coordination.dispatcher import CoordinationDispatcher

    store = CoordinationStore(tmp_path / "auto_dispatch.db")
    engine = CoordinationWorkflowEngine(store, WorkspaceCoordinator())
    run = await engine.start_plan_implement_review_run(
        title="Dispatch Auto",
        root_session_id="conv_root_dispatch",
        planner_session_id="conv_planner_dispatch",
        implementer_session_id="conv_coder_dispatch",
        reviewer_session_id="conv_reviewer_dispatch",
        user_prompt="Dispatch auto advance",
        workspace_path=str(tmp_path),
    )
    tasks = {t.assignee_role: t for t in store.list_tasks(run.run_id)}

    router = _DispatchRunnerRouter()
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: router,
    )
    dispatcher = CoordinationDispatcher(store)
    count = await dispatcher.dispatch_once()
    assert count == 1
    kickoff = store.list_messages(run.root_session_id)[0]
    assert kickoff.message_state == "active"
    assert router.called_session_ids == ["conv_planner_dispatch"]

    fake = _FakeConversationStore(
        _assistant_item("Plan ready.\n[WORKFLOW_RESULT: succeeded]", "resp_dispatch")
    )
    advancer = WorkflowAutoAdvancer(engine, fake)  # type: ignore[arg-type]
    session_live_state.configure(fake, None, store, advancer)  # type: ignore[arg-type]
    try:
        session_live_state.persist_a2a_turn_completed(
            "conv_planner_dispatch", "resp_dispatch"
        )
        _wait_until(
            lambda: store.get_task(tasks["implementer"].task_id).status == "running"
        )
    finally:
        session_live_state.configure(None)

    assert store.get_task(tasks["planner"].task_id).status == "succeeded"
    assert store.get_message(kickoff.message_id).consumption_state == "consumed"
