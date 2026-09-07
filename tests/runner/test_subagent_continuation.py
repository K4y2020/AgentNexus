import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from omnigent.runner import app
from omnigent.runner import tool_dispatch as dispatch


@pytest.mark.asyncio
@pytest.mark.parametrize("independent", [False, True])
async def test_new_title_requires_explicit_independent_task_decision(monkeypatch, independent):
    monkeypatch.setattr(app, "get_session_agent_id", lambda _: "parent-agent")
    monkeypatch.setattr(dispatch, "_has_subagent", lambda *_: True)
    monkeypatch.setattr(dispatch, "_session_turn_actor", AsyncMock(return_value=None))
    monkeypatch.setattr(dispatch, "_find_existing_child_session", AsyncMock(return_value=None))
    monkeypatch.setattr(dispatch, "_subagent_harness", lambda *_: None)
    preference = AsyncMock(side_effect=ValueError("model-validation-reached"))
    monkeypatch.setattr(dispatch, "_preferred_subagent_model", preference)
    monkeypatch.setattr(
        dispatch,
        "_list_child_sessions",
        AsyncMock(
            return_value=[
                {"id": "original", "tool": "codex", "title": "codex:fix-modal"},
            ]
        ),
    )
    async with httpx.AsyncClient(base_url="http://unused") as client:
        try:
            args = {"agent": "codex", "title": "cleanup", "args": "fix review findings"}
            if independent:
                args["new_task_reason"] = "Independent review in a separate context"
            result = await dispatch._execute_subagent_tool(
                args,
                server_client=client,
                conversation_id="parent",
                session_inbox=asyncio.Queue(),
            )
        finally:
            app._session_inboxes_ref.pop("parent", None)
    if independent:
        assert "model-validation-reached" in result
        preference.assert_awaited_once()
        return
    preference.assert_not_awaited()
    result = json.loads(result)
    assert result["error"] == "continuation_decision_required"
    assert result["existing_tasks"][0]["task_id"] == "original"


@pytest.mark.asyncio
async def test_task_id_rejects_ambiguous_address():
    result = await dispatch._execute_subagent_tool(
        {"task_id": "original", "agent": "codex", "args": "continue"},
    )
    assert "cannot be combined" in result


@pytest.mark.asyncio
async def test_continuation_lookup_includes_older_pages():
    def handle(request):
        if request.url.params.get("after") == "recent":
            return httpx.Response(200, json={"data": [{"id": "original"}], "has_more": False})
        return httpx.Response(
            200,
            json={
                "data": [{"id": "recent"}],
                "last_id": "recent",
                "has_more": True,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://server",
        transport=httpx.MockTransport(handle),
    ) as client:
        result = await dispatch._list_child_sessions(
            server_client=client,
            conversation_id="parent",
            exhaustive=True,
        )
    assert [child["id"] for child in result] == ["recent", "original"]
