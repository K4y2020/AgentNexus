"""Unit tests for Seedance V3 API client."""

from __future__ import annotations

import httpx
import pytest
import respx

from omnigent.seedance.client import (
    DEFAULT_SEEDANCE_BASE_URL,
    SeedanceAuthError,
    SeedanceClient,
    SeedanceConnectionError,
    SeedanceNotFoundError,
    SeedanceSecurityError,
    SeedanceSessionBusyError,
    SeedanceTimeoutError,
    get_seedance_api_key,
    validate_seedance_base_url,
)

_BASE = "http://127.0.0.1:8893"


def test_validate_seedance_base_url_security():
    assert validate_seedance_base_url("http://127.0.0.1:8893") == "http://127.0.0.1:8893"
    assert validate_seedance_base_url("http://localhost:8893/") == "http://localhost:8893"
    assert validate_seedance_base_url("http://[::1]:8893") == "http://[::1]:8893"

    with pytest.raises(SeedanceSecurityError, match="not an allowed local/loopback address"):
        validate_seedance_base_url("http://198.51.100.1:8893")

    with pytest.raises(SeedanceSecurityError, match="Invalid Seedance URL scheme"):
        validate_seedance_base_url("ftp://127.0.0.1:8893")


def test_get_seedance_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("SEEDANCE_API_KEY", "test-secret-key-123")
    assert get_seedance_api_key() == "test-secret-key-123"


@pytest.mark.asyncio
@respx.mock
async def test_healthz_success():
    respx.get(f"{_BASE}/healthz").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    async with SeedanceClient(base_url=_BASE) as client:
        result = await client.healthz()
        assert result["status"] == "ok"
        assert result["base_url"] == _BASE


@pytest.mark.asyncio
@respx.mock
async def test_healthz_connection_error():
    respx.get(f"{_BASE}/healthz").mock(side_effect=httpx.ConnectError("refused"))
    async with SeedanceClient(base_url=_BASE) as client:
        with pytest.raises(SeedanceConnectionError):
            await client.healthz()


@pytest.mark.asyncio
@respx.mock
async def test_healthz_timeout():
    respx.get(f"{_BASE}/healthz").mock(side_effect=httpx.TimeoutException("timeout"))
    async with SeedanceClient(base_url=_BASE) as client:
        with pytest.raises(SeedanceTimeoutError):
            await client.healthz()


@pytest.mark.asyncio
@respx.mock
async def test_create_project_success():
    respx.post(f"{_BASE}/v3/projects").mock(
        return_value=httpx.Response(201, json={"project": {"id": "proj_123", "name": "Cine Test"}})
    )
    async with SeedanceClient(base_url=_BASE, api_key="test-key") as client:
        proj = await client.create_project("Cine Test")
        assert proj["id"] == "proj_123"
        assert proj["name"] == "Cine Test"


@pytest.mark.asyncio
@respx.mock
async def test_create_agent_session_success():
    respx.post(f"{_BASE}/v3/projects/proj_123/agent-sessions").mock(
        return_value=httpx.Response(201, json={"session": {"id": "sess_456", "projectId": "proj_123"}})
    )
    async with SeedanceClient(base_url=_BASE, api_key="test-key") as client:
        sess = await client.create_agent_session("proj_123", model="gemini-3.7-flash-high")
        assert sess["id"] == "sess_456"
        assert sess["projectId"] == "proj_123"


@pytest.mark.asyncio
@respx.mock
async def test_send_agent_message_busy_409():
    respx.post(f"{_BASE}/v3/agent-sessions/sess_456/messages").mock(
        return_value=httpx.Response(
            409,
            json={"accepted": False, "code": "AGENT_SESSION_BUSY", "message": "Session is busy."},
        )
    )
    async with SeedanceClient(base_url=_BASE, api_key="test-key") as client:
        with pytest.raises(SeedanceSessionBusyError, match="busy"):
            await client.send_agent_message("sess_456", "Hello")


@pytest.mark.asyncio
@respx.mock
async def test_send_agent_message_timeout_504():
    respx.post(f"{_BASE}/v3/agent-sessions/sess_456/messages").mock(
        return_value=httpx.Response(504, json={"accepted": False, "code": "AGENT_TIMEOUT"})
    )
    async with SeedanceClient(base_url=_BASE, api_key="test-key") as client:
        with pytest.raises(SeedanceTimeoutError):
            await client.send_agent_message("sess_456", "Run long task")


@pytest.mark.asyncio
@respx.mock
async def test_send_agent_message_unauthorized_401():
    respx.post(f"{_BASE}/v3/agent-sessions/sess_456/messages").mock(
        return_value=httpx.Response(401, json={"code": "UNAUTHORIZED"})
    )
    async with SeedanceClient(base_url=_BASE, api_key="wrong-key") as client:
        with pytest.raises(SeedanceAuthError):
            await client.send_agent_message("sess_456", "Hello")


@pytest.mark.asyncio
@respx.mock
async def test_send_agent_message_not_found_404():
    respx.post(f"{_BASE}/v3/agent-sessions/sess_missing/messages").mock(
        return_value=httpx.Response(404, json={"code": "SESSION_NOT_FOUND"})
    )
    async with SeedanceClient(base_url=_BASE, api_key="test-key") as client:
        with pytest.raises(SeedanceNotFoundError):
            await client.send_agent_message("sess_missing", "Hello")


@pytest.mark.asyncio
@respx.mock
async def test_get_snapshot_and_jobs():
    respx.get(f"{_BASE}/v3/projects/proj_123/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "snapshot": {
                    "nodes": [{"id": "node_1", "type": "storyboard"}],
                    "jobs": [{"id": "job_1", "status": "succeeded"}],
                    "assets": [{"id": "asset_1", "filename": "shot1.png"}],
                }
            },
        )
    )
    respx.get(f"{_BASE}/v3/jobs/job_1").mock(
        return_value=httpx.Response(200, json={"job": {"id": "job_1", "status": "succeeded"}})
    )
    respx.post(f"{_BASE}/v3/jobs/job_1/cancel").mock(
        return_value=httpx.Response(200, json={"accepted": True})
    )

    async with SeedanceClient(base_url=_BASE, api_key="test-key") as client:
        snap = await client.get_snapshot("proj_123")
        assert len(snap["nodes"]) == 1
        assert snap["jobs"][0]["id"] == "job_1"

        job = await client.get_job("job_1")
        assert job["status"] == "succeeded"

        cancel_res = await client.cancel_job("job_1")
        assert cancel_res["accepted"] is True
