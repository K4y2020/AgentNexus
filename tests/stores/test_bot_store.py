"""Persistence tests for durable Bots and their singleton Sessions."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from agentnexus.stores.bot_store.sqlalchemy_store import SqlAlchemyBotStore
from agentnexus.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


def _id(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def test_ensure_bot_and_binding_are_idempotent(db_uri: str, tmp_path: Path) -> None:
    store = SqlAlchemyBotStore(db_uri)
    first = store.ensure_for_agent(
        owner_id="local",
        agent_id=_id("polly-agent"),
        name="polly",
        description="coding bot",
    )
    second = store.ensure_for_agent(
        owner_id="local",
        agent_id=_id("polly-agent"),
        name="polly changed upstream",
        description=None,
    )
    assert second.id == first.id
    assert second.name == "polly"

    home = str(tmp_path / "polly-home")
    binding = store.ensure_binding(bot_id=first.id, host_id=_id("host"), home_path=home)
    repeated = store.ensure_binding(bot_id=first.id, host_id=None, home_path="ignored")
    assert repeated.id == binding.id
    assert repeated.home_path == home


def test_bot_settings_are_owner_scoped(db_uri: str) -> None:
    store = SqlAlchemyBotStore(db_uri)
    bot = store.ensure_for_agent(
        owner_id="alice",
        agent_id=_id("agent"),
        name="Debby",
        description=None,
    )
    assert store.get(bot.id, owner_id="bob") is None
    updated = store.update_settings(
        bot.id,
        owner_id="alice",
        name=bot.name,
        description=bot.description,
        status="active",
        default_model="gemini-3.8-flash-high",
        behavior_mode="lean",
    )
    assert updated is not None
    assert updated.default_model == "gemini-3.8-flash-high"
    assert updated.behavior_mode == "lean"


def test_primary_and_a2a_slots_are_unique_per_bot(db_uri: str) -> None:
    bots = SqlAlchemyBotStore(db_uri)
    conversations = SqlAlchemyConversationStore(db_uri)
    bot = bots.ensure_for_agent(
        owner_id="local",
        agent_id=_id("agent"),
        name="Polly",
        description=None,
    )
    primary = conversations.create_conversation(
        agent_id=bot.agent_id,
        bot_id=bot.id,
        purpose="primary",
        singleton_slot="primary",
    )
    found = conversations.get_bot_singleton_session(bot.id, "primary")
    assert found is not None and found.id == primary.id
    with pytest.raises(IntegrityError):
        conversations.create_conversation(
            agent_id=bot.agent_id,
            bot_id=bot.id,
            purpose="primary",
            singleton_slot="primary",
        )
    assert len(conversations.list_conversations(agent_id=bot.agent_id, limit=100).data) == 1

    for _ in range(3):
        conversations.create_conversation(
            agent_id=bot.agent_id,
            bot_id=bot.id,
            purpose="topic",
        )
