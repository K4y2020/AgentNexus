"""Tests for runner dispatch of seedance_agent_message."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from omnigent.runner.tool_dispatch import (
    execute_tool,
    should_dispatch_locally,
)
from omnigent.spec.types import AgentSpec
from omnigent.tools.manager import ToolManager


def test_should_dispatch_seedance_locally():
    assert should_dispatch_locally("seedance_agent_message") is True


def test_tool_manager_has_seedance_agent_message():
    manager = ToolManager(AgentSpec(spec_version=1))
    tool = manager.get_tool("seedance_agent_message")
    assert tool is not None
    schema = tool.get_schema()
    assert schema["function"]["name"] == "seedance_agent_message"
    assert "task" in schema["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_seedance_dispatch_fails_without_server_client():
    res = await execute_tool(
        tool_name="seedance_agent_message",
        arguments=json.dumps({"task": "Test task"}),
        conversation_id="conv_123",
        server_client=None,
    )
    data = json.loads(res)
    assert "error" in data
    assert "server access" in data["error"]


@pytest.mark.asyncio
async def test_seedance_dispatch_fails_without_conversation_id():
    async with httpx.AsyncClient() as client:
        res = await execute_tool(
            tool_name="seedance_agent_message",
            arguments=json.dumps({"task": "Test task"}),
            conversation_id="",
            server_client=client,
        )
        data = json.loads(res)
        assert "error" in data
        assert "conversation id" in data["error"]


@pytest.mark.asyncio
async def test_seedance_dispatch_fails_with_empty_task():
    async with httpx.AsyncClient() as client:
        res = await execute_tool(
            tool_name="seedance_agent_message",
            arguments=json.dumps({"task": ""}),
            conversation_id="conv_123",
            server_client=client,
        )
        data = json.loads(res)
        assert "error" in data
        assert "task" in data["error"]


@pytest.mark.asyncio
async def test_seedance_dispatch_success_payload():
    mock_result = {
        "status": "completed",
        "outcome": "succeeded",
        "target_teammate": "seedance",
        "seedance_project_id": "proj_mock_1",
        "seedance_agent_session_id": "sess_mock_1",
        "seedance_canvas_url": "http://127.0.0.1:5173/?project=proj_mock_1",
        "seedance_job_ids": ["job_mock_1"],
        "seedance_asset_ids": [],
        "channel_scope": "topic:conv_123",
        "summary": "Sync OK",
        "generation_allowed": False,
    }

    with patch(
        "omnigent.seedance.bridge.execute_seedance_agent_message",
        new=AsyncMock(return_value=mock_result),
    ):
        async with httpx.AsyncClient() as client:
            res = await execute_tool(
                tool_name="seedance_agent_message",
                arguments=json.dumps({
                    "task": "Sync shot S01-01 to canvas",
                    "shot_ids": ["S01-01"],
                    "generation_allowed": False,
                }),
                conversation_id="conv_123",
                server_client=client,
            )
            data = json.loads(res)
            assert data["outcome"] == "succeeded"
            assert data["seedance_project_id"] == "proj_mock_1"
            assert data["seedance_canvas_url"] == "http://127.0.0.1:5173/?project=proj_mock_1"
            assert data["seedance_job_ids"] == ["job_mock_1"]


def test_should_dispatch_seedance_read_canvas_locally():
    assert should_dispatch_locally("seedance_read_canvas") is True


def test_tool_manager_has_seedance_read_canvas():
    manager = ToolManager(AgentSpec(spec_version=1))
    tool = manager.get_tool("seedance_read_canvas")
    assert tool is not None
    schema = tool.get_schema()
    assert schema["function"]["name"] == "seedance_read_canvas"
    assert "project_id" in schema["function"]["parameters"]["properties"]
    assert "node_types" in schema["function"]["parameters"]["properties"]
    assert "shot_ids" in schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_seedance_read_canvas_fails_without_server_client():
    res = await execute_tool(
        tool_name="seedance_read_canvas",
        arguments=json.dumps({}),
        conversation_id="conv_123",
        server_client=None,
    )
    data = json.loads(res)
    assert "error" in data
    assert "server access" in data["error"]


@pytest.mark.asyncio
async def test_seedance_read_canvas_dispatch_success():
    mock_result = {
        "status": "completed",
        "outcome": "succeeded",
        "bound": True,
        "seedance_project_id": "proj_mock_canvas",
        "seedance_canvas_url": "http://127.0.0.1:5173/?project=proj_mock_canvas",
        "revision": 3,
        "counts": {
            "total_nodes": 4,
            "image_prompts": 1,
            "video_prompts": 2,
            "storyboards": 1,
            "scripts": 0,
            "edges": 3,
        },
        "nodes": [
            {"id": "n1", "type": "storyboard", "title": "Episode 1 Outline"},
            {"id": "n2", "type": "video_prompt", "title": "S01-01 High Angle"},
        ],
        "edges": [{"id": "e1", "from": "n1", "to": "n2"}],
        "summary": "Read 4 nodes",
        "summary_markdown": "### Seedance 画布内容概览\n- S01-01",
    }

    with patch(
        "omnigent.seedance.bridge.read_seedance_canvas_snapshot",
        new=AsyncMock(return_value=mock_result),
    ):
        async with httpx.AsyncClient() as client:
            res = await execute_tool(
                tool_name="seedance_read_canvas",
                arguments=json.dumps({
                    "detail_level": "summary",
                    "include_edges": True,
                }),
                conversation_id="conv_123",
                server_client=client,
            )
            data = json.loads(res)
            assert data["outcome"] == "succeeded"
            assert data["seedance_project_id"] == "proj_mock_canvas"
            assert data["counts"]["total_nodes"] == 4
            assert len(data["nodes"]) == 2
            assert "summary_markdown" in data


def test_should_dispatch_seedance_edit_canvas_locally():
    assert should_dispatch_locally("seedance_edit_canvas") is True


def test_tool_manager_has_seedance_edit_canvas():
    manager = ToolManager(AgentSpec(spec_version=1))
    tool = manager.get_tool("seedance_edit_canvas")
    assert tool is not None
    schema = tool.get_schema()
    assert schema["function"]["name"] == "seedance_edit_canvas"
    props = schema["function"]["parameters"]["properties"]
    assert "action" in props
    assert "node_id" in props
    assert "prompt" in props
    assert "expected_revision" in props
    assert "confirm" in props
    assert "generation_allowed" in props


@pytest.mark.asyncio
async def test_seedance_edit_canvas_fails_without_action():
    async with httpx.AsyncClient() as client:
        res = await execute_tool(
            tool_name="seedance_edit_canvas",
            arguments=json.dumps({}),
            conversation_id="conv_123",
            server_client=client,
        )
        data = json.loads(res)
        assert "error" in data
        assert "action" in data["error"]


@pytest.mark.asyncio
async def test_seedance_edit_canvas_update_success():
    mock_result = {
        "status": "completed",
        "outcome": "succeeded",
        "action": "update_node",
        "seedance_project_id": "proj_mock",
        "verified": True,
        "node_id": "node_s03",
        "new_revision": 2,
        "node": {"id": "node_s03", "title": "S03 - Extreme Close-Up", "revision": 2},
        "summary": "Updated node_s03",
    }

    with patch(
        "omnigent.seedance.bridge.execute_seedance_canvas_edit",
        new=AsyncMock(return_value=mock_result),
    ):
        async with httpx.AsyncClient() as client:
            res = await execute_tool(
                tool_name="seedance_edit_canvas",
                arguments=json.dumps({
                    "action": "update_node",
                    "node_id": "node_s03",
                    "prompt": "New prompt for shot 3",
                    "expected_revision": 1,
                }),
                conversation_id="conv_123",
                server_client=client,
            )
            data = json.loads(res)
            assert data["outcome"] == "succeeded"
            assert data["verified"] is True
            assert data["new_revision"] == 2
