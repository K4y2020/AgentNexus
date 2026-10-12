"""Tests for runner dispatch of the Seedance canvas tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentnexus.runner.tool_dispatch import (
    execute_tool,
    should_dispatch_locally,
)
from agentnexus.spec.types import AgentSpec, BuiltinToolConfig, ToolsConfig
from agentnexus.tools.manager import ToolManager


def _canvas_tool_manager() -> ToolManager:
    names = ("seedance_read_canvas", "seedance_edit_canvas")
    return ToolManager(
        AgentSpec(
            spec_version=1,
            tools=ToolsConfig(builtins=[BuiltinToolConfig(name=name) for name in names]),
        )
    )


def test_removed_agent_message_is_not_dispatched():
    assert should_dispatch_locally("seedance_agent_message") is False


def test_quality_review_is_opt_in_and_has_no_model_supplied_verdict():
    assert should_dispatch_locally("cine_review_media")
    manager = ToolManager(
        AgentSpec(
            spec_version=1,
            tools=ToolsConfig(builtins=[BuiltinToolConfig(name="cine_review_media")]),
        )
    )
    schema = manager.get_tool("cine_review_media").get_schema()["function"]["parameters"]
    assert set(schema["properties"]) == {"production_dir"}
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_quality_review_dispatches_to_independent_runner():
    with patch(
        "agentnexus.seedance.media_quality.review_media",
        new=AsyncMock(
            return_value={"status": "reviewed", "envelope": {"signature": "runner-result"}}
        ),
    ) as review:
        async with httpx.AsyncClient() as client:
            result = json.loads(
                await execute_tool(
                    tool_name="cine_review_media",
                    arguments=json.dumps({"production_dir": "production"}),
                    conversation_id="topic",
                    server_client=client,
                )
            )
    assert result["status"] == "reviewed"
    assert review.await_args.kwargs["production_dir"] == "production"


@pytest.mark.asyncio
async def test_removed_agent_message_does_not_reach_the_bridge():
    async with httpx.AsyncClient() as client:
        res = await execute_tool(
            tool_name="seedance_agent_message",
            arguments=json.dumps({"task": "Sync shot S01-01"}),
            conversation_id="conv_123",
            server_client=client,
        )
    assert "not in local dispatch table" in res


def test_should_dispatch_seedance_read_canvas_locally():
    assert should_dispatch_locally("seedance_read_canvas") is True


def test_tool_manager_has_seedance_read_canvas():
    manager = _canvas_tool_manager()
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
async def test_seedance_read_canvas_fails_without_conversation_id():
    async with httpx.AsyncClient() as client:
        res = await execute_tool(
            tool_name="seedance_read_canvas",
            arguments=json.dumps({}),
            conversation_id="",
            server_client=client,
        )
    data = json.loads(res)
    assert "conversation id" in data["error"]


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
        "agentnexus.seedance.bridge.read_seedance_canvas_snapshot",
        new=AsyncMock(return_value=mock_result),
    ):
        async with httpx.AsyncClient() as client:
            res = await execute_tool(
                tool_name="seedance_read_canvas",
                arguments=json.dumps(
                    {
                        "detail_level": "summary",
                        "include_edges": True,
                    }
                ),
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
    manager = _canvas_tool_manager()
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
    assert "generation_request_id" in props
    assert "expected_continuation_job_id" in props
    assert props["sync_prompt"]["type"] == "boolean"
    assert "export_video" in props["action"]["enum"]


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
        "agentnexus.seedance.bridge.execute_seedance_canvas_edit",
        new=AsyncMock(return_value=mock_result),
    ):
        async with httpx.AsyncClient() as client:
            res = await execute_tool(
                tool_name="seedance_edit_canvas",
                arguments=json.dumps(
                    {
                        "action": "update_node",
                        "node_id": "node_s03",
                        "prompt": "New prompt for shot 3",
                        "expected_revision": 1,
                    }
                ),
                conversation_id="conv_123",
                server_client=client,
            )
            data = json.loads(res)
            assert data["outcome"] == "succeeded"
            assert data["verified"] is True
            assert data["new_revision"] == 2


@pytest.mark.asyncio
async def test_cast_validation_does_not_default_to_video():
    with patch(
        "agentnexus.seedance.bridge.execute_seedance_canvas_edit",
        new=AsyncMock(return_value={"status": "validated", "submitted": False}),
    ) as edit:
        async with httpx.AsyncClient() as client:
            await execute_tool(
                tool_name="seedance_edit_canvas",
                arguments=json.dumps(
                    {
                        "action": "validate_generation",
                        "production_stage": "cast",
                        "production_pointer": "/characters/0/image/sheet",
                    }
                ),
                conversation_id="conv_123",
                server_client=client,
            )
    assert edit.call_args.kwargs["generation_kind"] is None
    assert edit.call_args.kwargs["production_stage"] == "cast"


@pytest.mark.asyncio
async def test_generation_catalog_dispatch_is_read_only():
    with patch(
        "agentnexus.seedance.bridge.read_seedance_generation",
        new=AsyncMock(return_value={"status": "completed", "models": [{"value": "test-image"}]}),
    ) as read:
        async with httpx.AsyncClient() as client:
            result = await execute_tool(
                tool_name="seedance_read_canvas",
                arguments=json.dumps({"action": "models"}),
                conversation_id="topic",
                server_client=client,
            )
    assert json.loads(result)["models"][0]["value"] == "test-image"
    assert read.call_args.kwargs["action"] == "models"


@pytest.mark.asyncio
async def test_generation_request_identity_is_forwarded_without_coercion():
    with patch(
        "agentnexus.seedance.bridge.execute_seedance_canvas_edit",
        new=AsyncMock(return_value={"status": "submitted", "job": {"id": "job"}}),
    ) as edit:
        async with httpx.AsyncClient() as client:
            await execute_tool(
                tool_name="seedance_edit_canvas",
                arguments=json.dumps(
                    {
                        "action": "submit_generation",
                        "generation_allowed": True,
                        "generation_request_id": "plan-1:shot-1",
                        "expected_continuation_job_id": "previous-job",
                    }
                ),
                conversation_id="topic",
                server_client=client,
            )
    assert edit.call_args.kwargs["generation_request_id"] == "plan-1:shot-1"
    assert edit.call_args.kwargs["expected_continuation_job_id"] == "previous-job"


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "forwarded"), [(True, True), ("true", False), (None, False)])
async def test_preflight_prompt_sync_is_forwarded_only_as_a_real_boolean(value, forwarded):
    with patch(
        "agentnexus.seedance.bridge.execute_seedance_canvas_edit",
        new=AsyncMock(return_value={"status": "validated"}),
    ) as edit:
        async with httpx.AsyncClient() as client:
            await execute_tool(
                tool_name="seedance_edit_canvas",
                arguments=json.dumps({"action": "validate_generation", "sync_prompt": value}),
                conversation_id="topic",
                server_client=client,
            )
    assert edit.call_args.kwargs["sync_prompt"] is forwarded
