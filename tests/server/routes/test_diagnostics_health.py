"""Tests for the diagnostics health topology endpoint (``GET /v1/diagnostics/health``)."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from agentnexus.server.routes._sessions.common import (
    _LAST_TASK_ERROR_CODE_LABEL_KEY,
    _LAST_TASK_ERROR_LAYER_LABEL_KEY,
    _LAST_TASK_ERROR_MESSAGE_LABEL_KEY,
)
from agentnexus.stores import ConversationStore


async def test_get_diagnostics_health_default_shape(client: httpx.AsyncClient) -> None:
    """GET /v1/diagnostics/health returns a layered health topology with nodes and edges."""
    resp = await client.get("/v1/diagnostics/health")
    assert resp.status_code == 200
    body = resp.json()

    assert "generated_at" in body
    assert body["status"] in ("healthy", "degraded", "unhealthy", "unknown")
    assert "nodes" in body
    assert "edges" in body
    assert "error_clusters" in body
    assert "scanned_sessions" in body
    assert "scan_truncated" in body

    # Verify all expected layers are represented in nodes
    node_layers = {node["layer"] for node in body["nodes"]}
    expected_layers = {
        "server",
        "ui",
        "host",
        "runner",
        "harness",
        "provider",
        "tool",
        "workspace",
        "git",
        "policy",
    }
    assert expected_layers.issubset(node_layers)

    # Server node should be healthy and have version/pid/uptime metrics
    server_node = next(n for n in body["nodes"] if n["layer"] == "server")
    assert server_node["status"] == "healthy"
    assert "version" in server_node["metrics"]
    assert "uptime_s" in server_node["metrics"]

    # Edges define standard dependency topology
    edge_pairs = {(edge["source"], edge["target"]) for edge in body["edges"]}
    assert ("ui", "server") in edge_pairs
    assert ("server", "host") in edge_pairs
    assert ("server", "runner") in edge_pairs
    assert ("runner", "harness") in edge_pairs


async def test_get_diagnostics_health_error_clusters(
    app: FastAPI,
    client: httpx.AsyncClient,
) -> None:
    """Conversations with error labels are clustered by error layer and code."""
    conv_store: ConversationStore = app.state.conversation_store

    # Create a conversation with tool error labels
    conv = conv_store.create_conversation(title="Failed tool task")
    conv_store.set_labels(
        conv.id,
        {
            _LAST_TASK_ERROR_LAYER_LABEL_KEY: "tool",
            _LAST_TASK_ERROR_CODE_LABEL_KEY: "tool_execution_timeout",
            _LAST_TASK_ERROR_MESSAGE_LABEL_KEY: "Command timed out after 30s",
        },
    )

    resp = await client.get("/v1/diagnostics/health")
    assert resp.status_code == 200
    body = resp.json()

    # Tool node should report unhealthy and contain the error count
    tool_node = next(n for n in body["nodes"] if n["layer"] == "tool")
    assert tool_node["status"] == "unhealthy"
    assert tool_node["metrics"]["error_count"] >= 1
    assert tool_node["last_error"] is not None
    assert tool_node["last_error"]["code"] == "tool_execution_timeout"

    # Error clusters should list the tool error
    tool_cluster = next((c for c in body["error_clusters"] if c["layer"] == "tool"), None)
    assert tool_cluster is not None
    assert tool_cluster["count"] >= 1
    assert "tool_execution_timeout" in tool_cluster["codes"]
