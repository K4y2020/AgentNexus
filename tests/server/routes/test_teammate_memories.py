"""Tests for teammate memory CRUD (``/v1/teammates/{id}/memories``)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from omnigent.db.utils import generate_agent_id
from omnigent.native_coding_agents import CLAUDE_NATIVE_AGENT_NAME
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.routes.teammate_memories import create_teammate_memories_router
from omnigent.stores.agent_memory_store.sqlalchemy_store import (
    SqlAlchemyAgentMemoryStore,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def stores(db_uri: str, tmp_path: Path) -> dict[str, object]:
    """Real agent + memory stores sharing the test database."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    memory_store = SqlAlchemyAgentMemoryStore(db_uri)
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    cache = AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache")
    return {
        "agent_store": agent_store,
        "memory_store": memory_store,
        "agent_cache": cache,
    }


async def _client(stores: dict[str, object]) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(
        create_teammate_memories_router(
            stores["agent_store"],  # type: ignore[arg-type]
            stores["memory_store"],  # type: ignore[arg-type]
        ),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_memory_crud_lifecycle(stores: dict[str, object]) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="bot-agent", bundle_location="test:///bundle")

    async with await _client(stores) as client:
        resp = await client.get(f"/v1/teammates/{agent_id}/memories")
        assert resp.status_code == 200
        assert resp.json()["memories"] == []

        resp = await client.post(
            f"/v1/teammates/{agent_id}/memories",
            json={"content": "user prefers TypeScript"},
        )
        assert resp.status_code == 201, resp.text
        memory = resp.json()["memory"]
        memory_id = memory["id"]
        assert memory["agent_id"] == agent_id
        assert memory["content"] == "user prefers TypeScript"
        assert memory["source"] == "manual"

        resp = await client.patch(
            f"/v1/teammates/{agent_id}/memories/{memory_id}",
            json={"content": "user prefers Python"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["memory"]["content"] == "user prefers Python"

        resp = await client.get(f"/v1/teammates/{agent_id}/memories")
        assert resp.status_code == 200
        assert resp.json()["memories"][0]["id"] == memory_id
        assert resp.json()["memories"][0]["content"] == "user prefers Python"

        resp = await client.delete(f"/v1/teammates/{agent_id}/memories/{memory_id}")
        assert resp.status_code == 204

        resp = await client.get(f"/v1/teammates/{agent_id}/memories")
        assert resp.status_code == 200
        assert resp.json()["memories"] == []


async def test_memory_routes_reject_unknown_and_cli_wrapper_agents(
    stores: dict[str, object],
) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    native_id = generate_agent_id()
    agent_store.create(
        native_id,
        name=CLAUDE_NATIVE_AGENT_NAME,
        bundle_location="test:///bundle",
    )

    async with await _client(stores) as client:
        resp = await client.get(f"/v1/teammates/{generate_agent_id()}/memories")
        assert resp.status_code == 404

        resp = await client.post(
            f"/v1/teammates/{native_id}/memories",
            json={"content": "nope"},
        )
        assert resp.status_code == 404


async def test_memory_update_and_delete_are_agent_scoped(
    stores: dict[str, object],
) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    memory_store = stores["memory_store"]  # type: ignore[arg-type]
    owner_id = generate_agent_id()
    other_id = generate_agent_id()
    agent_store.create(owner_id, name="owner-bot", bundle_location="test:///bundle")
    agent_store.create(other_id, name="other-bot", bundle_location="test:///bundle")
    memory = memory_store.create(generate_agent_id(), owner_id, "secret fact")

    async with await _client(stores) as client:
        resp = await client.patch(
            f"/v1/teammates/{other_id}/memories/{memory.id}",
            json={"content": "hijack"},
        )
        assert resp.status_code == 404

        resp = await client.delete(f"/v1/teammates/{other_id}/memories/{memory.id}")
        assert resp.status_code == 404

async def test_memory_routes_reject_acp_cli_harness_agents(
    stores: dict[str, object],
) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    codebuddy_id = generate_agent_id()
    agent_store.create(
        codebuddy_id,
        name="codebuddy",
        bundle_location="test:///bundle",
    )

    async with await _client(stores) as client:
        resp = await client.get(f"/v1/teammates/{codebuddy_id}/memories")
        assert resp.status_code == 404

        resp = await client.post(
            f"/v1/teammates/{codebuddy_id}/memories",
            json={"content": "not-allowed"},
        )
        assert resp.status_code == 404
