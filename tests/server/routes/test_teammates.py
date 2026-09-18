"""Tests for the teammates roster route (``GET /v1/teammates``).

The route is intentionally read-only: it aggregates built-in agents plus the
caller's scheduled routines from the real stores. These tests keep the app
small (only the teammates router mounted) so the aggregation contract is
tested without the full server lifespan.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agentnexus.db.utils import generate_agent_id
from agentnexus.native_coding_agents import CLAUDE_NATIVE_AGENT_NAME
from agentnexus.runtime.agent_cache import AgentCache
from agentnexus.server.routes.teammates import create_teammates_router
from agentnexus.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from agentnexus.stores.artifact_store.local import LocalArtifactStore
from agentnexus.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from agentnexus.stores.scheduled_task_store.sqlalchemy_store import (
    SqlAlchemyScheduledTaskStore,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def stores(db_uri: str, tmp_path: Path) -> dict[str, object]:
    """Real stores + cache for the route, sharing one test database."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    scheduled_task_store = SqlAlchemyScheduledTaskStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    cache = AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache")
    return {
        "agent_store": agent_store,
        "scheduled_task_store": scheduled_task_store,
        "agent_cache": cache,
        "conversation_store": conversation_store,
    }


async def _client(stores: dict[str, object]) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(
        create_teammates_router(
            stores["agent_store"],  # type: ignore[arg-type]
            stores["scheduled_task_store"],  # type: ignore[arg-type]
            stores["agent_cache"],  # type: ignore[arg-type]
            conversation_store=stores.get("conversation_store"),  # type: ignore[arg-type]
        ),
        prefix="/v1",
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_teammates_aggregates_agent_and_routines(stores: dict[str, object]) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    scheduled_task_store = stores["scheduled_task_store"]  # type: ignore[arg-type]
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="teammate-a", bundle_location="test:///bundle")
    task = scheduled_task_store.create(
        scheduled_task_id=generate_agent_id(),
        name="daily digest",
        prompt="summarize yesterday",
        rrule="FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        user_id=None,
        agent_id=agent_id,
        timezone="UTC",
    )
    conversation_id = generate_agent_id()
    scheduled_task_store.update(
        task.id,
        last_run_at=1_700_000_000,
        last_run_conversation_id=conversation_id,
    )
    scheduled_task_store.create_run(
        generate_agent_id(),
        task.id,
        "succeeded",
        1_700_000_000,
        conversation_id=conversation_id,
        finished_at=1_700_000_100,
    )

    async with await _client(stores) as client:
        resp = await client.get("/v1/teammates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["teammates"]) == 1
    teammate = body["teammates"][0]
    assert teammate["bot"]["agent_id"] == agent_id
    assert teammate["bot"]["home_path"].endswith(teammate["bot"]["id"])
    assert teammate["agent"]["id"] == agent_id
    assert teammate["agent"]["name"] == "teammate-a"
    assert teammate["routine_count"] == 1
    assert teammate["last_activity_at"] == 1_700_000_000
    assert teammate["last_activity_status"] == "succeeded"
    assert teammate["last_activity_conversation_id"] == conversation_id
    routine = teammate["routines"][0]
    assert routine["id"] == task.id
    assert routine["name"] == "daily digest"
    assert routine["state"] == "active"
    assert routine["last_run_status"] == "succeeded"


async def test_teammates_backfills_primary_a2a_and_topics_once(
    stores: dict[str, object],
) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    conversation_store = stores["conversation_store"]  # type: ignore[arg-type]
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="polly", bundle_location="test:///bundle")
    primary = conversation_store.create_conversation(agent_id=agent_id, title="Polly")
    a2a = conversation_store.create_conversation(agent_id=agent_id, title="A2A")
    topic = conversation_store.create_conversation(agent_id=agent_id, title="Topic")
    conversation_store.set_labels(primary.id, {"agentnexus.teammate.primary": "true"})
    conversation_store.set_labels(a2a.id, {"agentnexus.teammate.channel": "a2a"})

    async with await _client(stores) as client:
        first = await client.get("/v1/teammates")
        second = await client.get("/v1/teammates")
    assert first.status_code == second.status_code == 200
    bot_id = first.json()["teammates"][0]["bot"]["id"]
    assert conversation_store.get_conversation(primary.id).bot_id == bot_id
    assert conversation_store.get_conversation(primary.id).purpose == "primary"
    assert conversation_store.get_conversation(a2a.id).purpose == "a2a"
    assert conversation_store.get_conversation(topic.id).purpose == "topic"


async def test_teammates_agent_without_routines_has_empty_lists(
    stores: dict[str, object],
) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="quiet-agent", bundle_location="test:///bundle")

    async with await _client(stores) as client:
        resp = await client.get("/v1/teammates")
    assert resp.status_code == 200
    teammate = resp.json()["teammates"][0]
    assert teammate["agent"]["id"] == agent_id
    assert teammate["routines"] == []
    assert teammate["routine_count"] == 0
    assert teammate["last_activity_at"] is None


async def test_teammates_excludes_native_cli_wrapper_agents(
    stores: dict[str, object],
) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    bot_id = generate_agent_id()
    agent_store.create(bot_id, name="bot-agent", bundle_location="test:///bundle")
    agent_store.create(
        generate_agent_id(),
        name=CLAUDE_NATIVE_AGENT_NAME,
        bundle_location="test:///bundle",
    )

    async with await _client(stores) as client:
        resp = await client.get("/v1/teammates")
    assert resp.status_code == 200
    teammates = resp.json()["teammates"]
    assert [t["agent"]["name"] for t in teammates] == ["bot-agent"]


async def test_teammates_resolves_primary_conversation(
    stores: dict[str, object],
    db_uri: str,
) -> None:
    from agentnexus.stores.conversation_store.sqlalchemy_store import (
        SqlAlchemyConversationStore,
    )

    conv_store = SqlAlchemyConversationStore(db_uri)
    stores["conversation_store"] = conv_store

    agent_store = stores["agent_store"]  # type: ignore[assignment]
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="bot-with-dm", bundle_location="test:///bundle")

    primary_conv_id = generate_agent_id()
    conv_store.create_conversation(
        conversation_id=primary_conv_id,
        agent_id=agent_id,
        title="bot-with-dm",
    )
    conv_store.set_labels(primary_conv_id, {"agentnexus.teammate.primary": "true"})

    async with await _client(stores) as client:
        resp = await client.get("/v1/teammates")
    assert resp.status_code == 200
    body = resp.json()
    teammate = next(t for t in body["teammates"] if t["agent"]["id"] == agent_id)
    assert teammate["primary_conversation_id"] == primary_conv_id

async def test_teammates_excludes_acp_cli_harness_agents(
    stores: dict[str, object],
) -> None:
    agent_store = stores["agent_store"]  # type: ignore[arg-type]
    bot_id = generate_agent_id()
    agent_store.create(bot_id, name="bot-agent", bundle_location="test:///bundle")
    agent_store.create(
        generate_agent_id(),
        name="codebuddy",
        bundle_location="test:///bundle",
    )
    agent_store.create(
        generate_agent_id(),
        name="devin",
        bundle_location="test:///bundle",
    )

    async with await _client(stores) as client:
        resp = await client.get("/v1/teammates")
    assert resp.status_code == 200
    teammates = resp.json()["teammates"]
    assert [t["agent"]["name"] for t in teammates] == ["bot-agent"]
