"""
Unit tests for Cine Camera Evidence RAG tool and engine.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from agentnexus.tools.builtins.cine_camera_evidence import CineCameraEvidenceTool
from agentnexus.tools.base import ToolContext


def test_cine_camera_evidence_tool_schema():
    tool = CineCameraEvidenceTool()
    assert tool.name() == "cine_camera_evidence"
    schema = tool.get_schema()
    assert schema["type"] == "function"
    params = schema["function"]["parameters"]["properties"]
    assert "query" in params
    assert "brief" in params
    assert "motion" in params
    assert "limit" in params


def test_cine_camera_evidence_invoke_query():
    tool = CineCameraEvidenceTool()
    ctx = ToolContext("test-task-1", "test-agent-1")
    raw = tool.invoke(json.dumps({"query": "慢推", "motion": "push-in", "limit": 2}), ctx)
    data = json.loads(raw)
    assert "totalMatched" in data
    assert data["totalMatched"] > 0
    assert len(data.get("occurrences", [])) <= 2


def test_cine_camera_evidence_invoke_brief_with_atoms():
    tool = CineCameraEvidenceTool()
    ctx = ToolContext("test-task-2", "test-agent-2")
    raw = tool.invoke(
        json.dumps({
            "brief": "低机位推镜，主角在暴雨中拔刀力劈",
            "include_atoms": True,
            "limit": 3,
        }),
        ctx,
    )
    data = json.loads(raw)
    assert "totalMatched" in data
    assert "intent" in data
    assert data["intent"]["initiative"] == "aggressor"
    assert "劈" in data["intent"]["actionSeeds"]
    assert len(data.get("atomHits", [])) > 0
