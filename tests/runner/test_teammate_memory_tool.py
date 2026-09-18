"""Unit tests for save_teammate_memory tool dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from agentnexus.runner.tool_dispatch import (
    execute_tool,
    should_dispatch_locally,
)
from agentnexus.spec.types import AgentSpec
from agentnexus.tools.manager import ToolManager


def test_should_dispatch_memory_locally():
    assert should_dispatch_locally("save_teammate_memory") is True


def test_tool_manager_has_save_teammate_memory():
    manager = ToolManager(AgentSpec(spec_version=1))
    tool = manager.get_tool("save_teammate_memory")
    assert tool is not None
    schema = tool.get_schema()
    assert schema["function"]["name"] == "save_teammate_memory"
    assert "content" in schema["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_save_memory_requires_content():
    async with httpx.AsyncClient() as client:
        res = await execute_tool(
            tool_name="save_teammate_memory",
            arguments=json.dumps({"content": ""}),
            conversation_id="conv_123",
            server_client=client,
        )
        data = json.loads(res)
        assert "error" in data
        assert "content" in data["error"]


@pytest.mark.asyncio
async def test_save_memory_success():
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/v1/teammates/agent_abc/memories" in str(request.url):
            return httpx.Response(
                201,
                json={
                    "memory": {
                        "id": "mem_123",
                        "agent_id": "agent_abc",
                        "content": "User prefers dark theme",
                    }
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await execute_tool(
            tool_name="save_teammate_memory",
            arguments=json.dumps({"content": "User prefers dark theme"}),
            conversation_id="conv_123",
            agent_id="agent_abc",
            server_client=client,
        )
        data = json.loads(res)
        assert data["status"] == "saved"
        assert data["memory_id"] == "mem_123"
        assert "User prefers dark theme" in data["content"]
