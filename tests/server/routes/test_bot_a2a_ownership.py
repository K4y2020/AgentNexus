"""A2A authorization is anchored to durable Bot ownership."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.coordination.store import CoordinationStore
from agentnexus.server.routes.coordination import router
from agentnexus.stores.bot_store.sqlalchemy_store import SqlAlchemyBotStore
from agentnexus.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


def _id(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def test_cross_bot_message_requires_target_a2a_channel(db_uri: str, tmp_path: Path) -> None:
    bots = SqlAlchemyBotStore(db_uri)
    conversations = SqlAlchemyConversationStore(db_uri)
    source_bot = bots.ensure_for_agent(
        owner_id="local",
        agent_id=_id("debby-agent"),
        name="Debby",
        description=None,
    )
    target_bot = bots.ensure_for_agent(
        owner_id="local",
        agent_id=_id("polly-agent"),
        name="Polly",
        description=None,
    )
    source = conversations.create_conversation(
        agent_id=source_bot.agent_id,
        bot_id=source_bot.id,
        purpose="primary",
        singleton_slot="primary",
    )
    target_a2a = conversations.create_conversation(
        agent_id=target_bot.agent_id,
        bot_id=target_bot.id,
        purpose="a2a",
        singleton_slot="a2a",
    )
    target_topic = conversations.create_conversation(
        agent_id=target_bot.agent_id,
        bot_id=target_bot.id,
        purpose="topic",
    )
    app = FastAPI()
    app.include_router(router)
    app.state.conversation_store = conversations
    app.state.bot_store = bots
    app.state.coordination_store = CoordinationStore(tmp_path / "coordination.db")
    app.state.permission_store = None
    client = TestClient(app)

    payload = {
        "root_session_id": source.id,
        "sender_session_id": source.id,
        "recipient_session_id": target_a2a.id,
        "payload": {"prompt": "implement this"},
    }
    accepted = client.post("/v1/coordination/messages", json=payload)
    assert accepted.status_code == 200, accepted.text
    saved_payload = accepted.json()["message"]["payload"]
    assert saved_payload["source_bot_id"] == source_bot.id
    assert saved_payload["target_bot_id"] == target_bot.id

    payload["recipient_session_id"] = target_topic.id
    rejected = client.post("/v1/coordination/messages", json=payload)
    assert rejected.status_code == 403
    assert "A2A channel" in rejected.json()["detail"]
