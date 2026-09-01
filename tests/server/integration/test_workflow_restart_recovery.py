"""Server-level recovery acceptance for the P4 workflow scheduler.

This test models a real crash window: a fixed workflow run is created, the
planner stage has already been lost, and the implementer task is running with
no durable dispatch because the server died between task transition and outbox
write. A brand-new app (fresh store object, fresh lifespan) must reconcile the
missing dispatch through the real outbox and RunnerRouter, then must NOT replay
the message once the recipient's consumption receipt is persisted.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from omnigent.coordination.store import CoordinationStore
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server import session_live_state
from omnigent.server.app import create_app
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore

pytestmark = pytest.mark.asyncio


async def _wait_until(predicate: Any, *, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
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
        self.runner_id = "runner_restart"
        self.client = client


class _RunnerRouter:
    def __init__(self, status_code: int = 200) -> None:
        self.client = _RunnerClient(status_code)
        self.called_session_ids: list[str] = []

    def client_for_session_resources(self, session_id: str) -> _RoutedRunner:
        self.called_session_ids.append(session_id)
        return _RoutedRunner(self.client)


def _build_app(db_uri: str, tmp_path: Path, suffix: str) -> FastAPI:
    artifact_store = LocalArtifactStore(str(tmp_path / f"artifacts-{suffix}"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(
            artifact_store=artifact_store,
            cache_dir=tmp_path / f"cache-{suffix}",
        ),
        coordination_store=CoordinationStore(db_uri),
    )


def _seed_coordination_tree(app: FastAPI) -> tuple[object, object, object, object]:
    store = app.state.conversation_store
    root = store.create_conversation(title="restart-root")
    planner = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="planner:restart",
    )
    implementer = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="implementer:restart",
    )
    reviewer = store.create_conversation(
        parent_conversation_id=root.id,
        kind="sub_agent",
        title="reviewer:restart",
    )
    return root, planner, implementer, reviewer


async def test_server_restart_recovers_torn_dispatch_and_never_replays_consumed(
    runtime_init: None,
    db_uri: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh server rebuild heals only the missing stage dispatch."""
    first_app = _build_app(db_uri, tmp_path, "first")
    transport = ASGITransport(app=first_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root, planner, implementer, reviewer = _seed_coordination_tree(first_app)
        store = first_app.state.coordination_store
        assert store is not None
        resp = await client.post(
            "/v1/coordination/workflows/plan-implement-review",
            json={
                "title": "Restart Recovery",
                "root_session_id": root.id,
                "planner_session_id": planner.id,
                "implementer_session_id": implementer.id,
                "reviewer_session_id": reviewer.id,
                "user_prompt": "Recover after a torn transition",
                "workspace_path": ".",
            },
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run"]["run_id"]
        tasks = await asyncio.to_thread(store.list_tasks, run_id)
        by_role = {task.assignee_role: task for task in tasks}

        kickoff = (await asyncio.to_thread(store.list_messages, root.id))[0]
        await asyncio.to_thread(store.cancel_message, kickoff.message_id)
        await asyncio.to_thread(
            store.transition_task_status,
            by_role["planner"].task_id,
            "succeeded",
            from_statuses=["assigned", "running"],
        )
        await asyncio.to_thread(
            store.transition_task_status,
            by_role["implementer"].task_id,
            "running",
            from_statuses=["queued", "assigned", "running"],
        )

        before = await asyncio.to_thread(store.list_messages, root.id)
        assert not any(msg.recipient_session_id == implementer.id for msg in before)
        current = await asyncio.to_thread(store.get_run, run_id)
        assert current is not None and current.status == "running"

    fake_router = _RunnerRouter()
    monkeypatch.setattr(
        "omnigent.server.routes._sessions.common.get_server_runner_router",
        lambda: fake_router,
    )

    second_app = _build_app(db_uri, tmp_path, "second")
    async with second_app.router.lifespan_context(second_app):
        transport = ASGITransport(app=second_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            store2 = second_app.state.coordination_store
            assert store2 is not None

            async def _implementer_active() -> bool:
                messages = await asyncio.to_thread(store2.list_messages, root.id)
                return any(
                    msg.recipient_session_id == implementer.id and msg.message_state == "active"
                    for msg in messages
                )

            await _wait_until(_implementer_active)
            recover_messages = await asyncio.to_thread(store2.list_messages, root.id)
            recovered = [
                msg for msg in recover_messages if msg.recipient_session_id == implementer.id
            ]
            assert len(recovered) == 1
            assert recovered[0].task_id == by_role["implementer"].task_id
            assert recovered[0].consumption_state == "unconsumed"
            assert implementer.id in fake_router.called_session_ids

            events = await asyncio.to_thread(store2.list_events, root.id)
            assert any(e.event_type == "workflow.dispatch.healed" for e in events)

            await asyncio.to_thread(
                store2.record_consumption_receipt,
                recovered[0].message_id,
                "consumed",
                {"source": "server-restart-recovery-test"},
            )
            recovered_ids = {msg.message_id for msg in recovered}

    third_app = _build_app(db_uri, tmp_path, "third")
    async with third_app.router.lifespan_context(third_app):
        transport = ASGITransport(app=third_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            store3 = third_app.state.coordination_store
            assert store3 is not None
            await asyncio.sleep(2.0)
            after = await asyncio.to_thread(store3.list_messages, root.id)
            after_recovered = {
                msg.message_id for msg in after if msg.recipient_session_id == implementer.id
            }
            assert after_recovered == recovered_ids
            events_after = await asyncio.to_thread(store3.list_events, root.id)
            healed = [e for e in events_after if e.event_type == "workflow.dispatch.healed"]
            assert len(healed) == 1

    session_live_state.configure(None)
