"""Tests for Phase 2 & 3 Bot Projects, Computers, and A2A ACL endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.coordination.store import CoordinationStore
from omnigent.server.routes.coordination import router as coord_router
from omnigent.server.routes.teammates import create_teammates_router
from omnigent.stores.bot_store.sqlalchemy_store import SqlAlchemyBotStore


@pytest.fixture
def test_app(tmp_path: Path) -> FastAPI:
    db_path = tmp_path / "test_api_p2.db"
    store = SqlAlchemyBotStore(f"sqlite:///{db_path}")
    coord_store = CoordinationStore(f"sqlite:///{db_path}")

    app = FastAPI()
    app.state.bot_store = store
    app.state.coordination_store = coord_store

    teammates_router = create_teammates_router(
        None,
        None,
        None,
        bot_store=store,
    )
    app.include_router(teammates_router, prefix="/v1")
    app.include_router(coord_router, prefix="/v1")
    return app


def test_bot_project_binding_routes(test_app: FastAPI, tmp_path: Path) -> None:
    client = TestClient(test_app)
    bot_store: SqlAlchemyBotStore = test_app.state.bot_store

    # Create a bot first
    bot = bot_store.ensure_for_agent(
        owner_id="local",
        agent_id=uuid.uuid4().hex,
        name="polly",
        description="test bot",
    )

    proj_dir = tmp_path / "my_project"
    proj_dir.mkdir(parents=True)
    proj_id = uuid.uuid4().hex

    # 1. Bind project via POST /v1/bots/{bot_id}/projects
    bind_res = client.post(
        f"/v1/bots/{bot.id}/projects",
        json={
            "project_id": proj_id,
            "checkout_root": str(proj_dir),
            "default_branch": "main",
        },
    )
    assert bind_res.status_code == 200
    assert bind_res.json()["binding"]["project_id"] == proj_id

    # 2. List projects via GET /v1/bots/{bot_id}/projects
    list_res = client.get(f"/v1/bots/{bot.id}/projects")
    assert list_res.status_code == 200
    projects = list_res.json()["projects"]
    assert len(projects) == 1
    assert projects[0]["project_id"] == proj_id

    # 3. Unbind project via DELETE /v1/bots/{bot_id}/projects/{project_id}
    del_res = client.delete(f"/v1/bots/{bot.id}/projects/{proj_id}")
    assert del_res.status_code == 200
    assert del_res.json()["unbound"] is True

    # 4. Verify list is now empty
    list_res_after = client.get(f"/v1/bots/{bot.id}/projects")
    assert len(list_res_after.json()["projects"]) == 0


def test_local_computer_capabilities_endpoint(test_app: FastAPI) -> None:
    client = TestClient(test_app)
    res = client.get("/v1/computers/local")
    assert res.status_code == 200
    comp = res.json()["computer"]
    assert comp["provider_kind"] == "local_host"
    assert comp["state"] == "running"
    assert comp["capabilities"]["supports_git_worktrees"] is True
    assert comp["capabilities"]["supports_leases"] is True
