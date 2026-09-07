"""Focused regressions for bounded session model binding."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnigent.errors import OmnigentError
from omnigent.server.routes._sessions.orchestration import (
    _bind_bot_default_model,
    _effective_saved_subagent_model_preference,
    _saved_subagent_model_preference,
)
from omnigent.server.schemas import SessionEventInput


class _BotStore:
    def __init__(self, default_model: str | None) -> None:
        self.default_model = default_model

    def get(self, _bot_id: str) -> SimpleNamespace:
        return SimpleNamespace(default_model=self.default_model)


@pytest.mark.asyncio
async def test_a2a_session_binds_own_bot_default_without_session_override() -> None:
    conv = SimpleNamespace(id="a2a", bot_id="target-bot", model_override=None)
    body = SessionEventInput(type="message", data={"role": "user", "content": []})

    bound = await _bind_bot_default_model(conv, body, _BotStore("gemini-3.8-flash-high"))

    assert bound.model_override == "gemini-3.8-flash-high"


@pytest.mark.asyncio
async def test_topic_explicit_session_override_beats_bot_default() -> None:
    conv = SimpleNamespace(id="topic", bot_id="target-bot", model_override="gpt-5.6-terra")
    body = SessionEventInput(type="message", data={"role": "user", "content": []})

    bound = await _bind_bot_default_model(conv, body, _BotStore("gemini-3.8-flash-high"))

    assert bound.model_override is None


@pytest.mark.asyncio
async def test_bot_model_settings_failure_fails_closed() -> None:
    class BrokenStore:
        def get(self, _bot_id: str) -> SimpleNamespace:
            raise OSError("settings unavailable")

    conv = SimpleNamespace(id="a2a", bot_id="target-bot", model_override=None)
    body = SessionEventInput(type="message", data={"role": "user", "content": []})

    with pytest.raises(OmnigentError, match="no fallback model was selected"):
        await _bind_bot_default_model(conv, body, BrokenStore())


def test_saved_child_preference_is_authoritative_for_routing() -> None:
    parent = SimpleNamespace(labels={"subagent.model.codex": "deepseek-v4-flash"})
    child = SimpleNamespace(sub_agent_name="codex")

    assert _saved_subagent_model_preference(child, parent) == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_empty_a2a_uses_same_bot_primary_worker_preference() -> None:
    primary = SimpleNamespace(
        id="primary",
        bot_id="polly",
        labels={"subagent.model.claude_code": "gemini-3.8-flash-high"},
    )
    a2a = SimpleNamespace(
        id="a2a",
        bot_id="polly",
        labels={},
        sub_agent_name="claude_code",
    )
    parent = SimpleNamespace(
        id="a2a-parent",
        bot_id="polly",
        labels={},
    )

    class ConversationStore:
        def get_bot_singleton_session(self, bot_id: str, slot: str) -> SimpleNamespace:
            assert bot_id == "polly"
            assert slot == "primary"
            return primary

    assert (
        await _effective_saved_subagent_model_preference(a2a, parent, ConversationStore())
        == "gemini-3.8-flash-high"
    )


@pytest.mark.asyncio
async def test_explicit_empty_worker_preference_blocks_primary_inheritance() -> None:
    primary = SimpleNamespace(
        id="primary",
        labels={"subagent.model.claude_code": "gemini-3.8-flash-high"},
    )
    parent = SimpleNamespace(
        id="a2a",
        bot_id="polly",
        labels={"subagent.model.claude_code": ""},
    )
    child = SimpleNamespace(sub_agent_name="claude_code")

    class ConversationStore:
        def get_bot_singleton_session(self, _bot_id: str, _slot: str) -> SimpleNamespace:
            return primary

    assert (
        await _effective_saved_subagent_model_preference(child, parent, ConversationStore()) == ""
    )
