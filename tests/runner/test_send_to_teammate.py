from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from omnigent.runner.tool_dispatch import _execute_send_to_teammate_tool


@pytest.mark.asyncio
async def test_send_to_teammate_binds_wakes_and_queues_once() -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/v1/teammates":
            return httpx.Response(
                200,
                json={
                    "teammates": [
                        {
                            "bot": {
                                "id": "bot_polly",
                                "host_id": "host_1",
                                "home_path": "C:/bot-homes/polly",
                                "default_model": "gpt-5.6-luna",
                            },
                            "agent": {"id": "agent_polly", "name": "polly"},
                            "primary_conversation_id": "polly_primary",
                        }
                    ]
                },
            )
        if request.url.path == "/v1/sessions":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "polly_a2a",
                            "purpose": "a2a",
                            "labels": {"omnigent.teammate.channel": "a2a"},
                        }
                    ]
                },
            )
        if request.url.path == "/v1/sessions/polly_primary":
            return httpx.Response(
                200,
                json={"host_id": "host_1", "workspace": "C:/workspaces/polly"},
            )
        if request.url.path == "/v1/sessions/polly_a2a":
            return httpx.Response(
                200,
                json={
                    "runner_id": None,
                    "host_id": None,
                    "workspace": "C:/workspaces/polly",
                    "runner_online": False,
                },
            )
        if request.url.path == "/v1/hosts/host_1/runners":
            return httpx.Response(200, json={"runner_id": "runner_1"})
        if request.url.path == "/v1/sessions/polly_a2a/events":
            assert body == {"type": "retry_session", "data": {}}
            return httpx.Response(202, json={"queued": False})
        if request.url.path == "/v1/sessions/debby_primary/items":
            return httpx.Response(200, json={"data": []})
        if request.url.path == "/v1/coordination/messages":
            return httpx.Response(
                200,
                json={"message": {"message_id": "msg_1"}, "delivery_state": "pending"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {"teammate": "polly", "task": "Reply received", "wait": False},
                server_client=client,
                conversation_id="debby_primary",
                agent_spec=SimpleNamespace(name="debby"),
            )
        )

    assert result["status"] == "dispatched"
    assert result["target_session_id"] == "polly_a2a"
    assert result["coordination_message_id"] == "msg_1"
    assert result["effective_model"] == "gpt-5.6-luna"
    assert result["model_source"] == "bot.default_model"
    assert (
        "POST",
        "/v1/hosts/host_1/runners",
        {"session_id": "polly_a2a", "workspace": str(Path("C:/bot-homes/polly") / "scratch")},
    ) in requests
    assert sum(path == "/v1/coordination/messages" for _, path, _ in requests) == 1
    coordination_request = next(
        body for _, path, body in requests if path == "/v1/coordination/messages"
    )
    assert coordination_request is not None
    assert str(coordination_request["idempotency_key"]).startswith("teammate:")
    assert not any(body and body.get("type") == "message" for _, _, body in requests)


@pytest.mark.asyncio
async def test_send_to_teammate_selects_the_current_topic_channel() -> None:
    coordination_body: dict[str, object] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal coordination_body
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        if path == "/v1/teammates":
            return httpx.Response(
                200,
                json={
                    "teammates": [
                        {
                            "bot": {
                                "id": "bot_polly",
                                "host_id": "host_1",
                                "home_path": "C:/bot-homes/polly",
                            },
                            "agent": {"id": "agent_polly", "name": "polly"},
                        }
                    ]
                },
            )
        if path == "/v1/sessions":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "polly_topic_a2a",
                            "purpose": "a2a",
                            "labels": {
                                "omnigent.teammate.channel": "a2a",
                                "omnigent.teammate.channel_scope": "topic:topic_a",
                                "omnigent.teammate.channel_kind": "topic",
                            },
                        },
                        {
                            "id": "polly_topic_b2a",
                            "purpose": "a2a",
                            "labels": {
                                "omnigent.teammate.channel": "a2a",
                                "omnigent.teammate.channel_scope": "topic:topic_b",
                                "omnigent.teammate.channel_kind": "topic",
                            },
                        },
                    ]
                },
            )
        if path == "/v1/sessions/topic_a":
            return httpx.Response(
                200,
                json={
                    "purpose": "topic",
                    "host_id": "host_1",
                    "root_conversation_id": "topic_a",
                },
            )
        if path == "/v1/sessions/polly_topic_a2a":
            return httpx.Response(
                200,
                json={
                    "purpose": "a2a",
                    "host_id": "host_1",
                    "runner_id": "runner_1",
                    "runner_online": True,
                    "labels": {
                        "omnigent.teammate.channel_scope": "topic:topic_a",
                    },
                },
            )
        if path.endswith("/events"):
            return httpx.Response(202, json={})
        if path == "/v1/coordination/messages":
            coordination_body = body
            return httpx.Response(
                200,
                json={"message": {"message_id": "msg_topic_a"}, "delivery_state": "pending"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {
                    "teammate": "polly",
                    "task": "Review Topic A",
                    "file_ids": [],
                },
                server_client=client,
                conversation_id="topic_a",
                agent_spec=SimpleNamespace(name="debby"),
            )
        )

    assert result["status"] == "dispatched"
    assert result["target_session_id"] == "polly_topic_a2a"
    assert result["channel_kind"] == "topic"
    assert result["channel_scope"] == "topic:topic_a"
    assert coordination_body is not None
    assert coordination_body["recipient_session_id"] == "polly_topic_a2a"
    assert coordination_body["payload"]["a2a_channel_scope"] == "topic:topic_a"


@pytest.mark.asyncio
async def test_send_to_teammate_copies_latest_user_attachments() -> None:
    coordination_body: dict[str, object] | None = None
    uploaded_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal coordination_body, uploaded_body
        content_type = request.headers.get("content-type", "")
        body = (
            json.loads(request.content)
            if request.content and content_type.startswith("application/json")
            else None
        )
        path = request.url.path
        if path == "/v1/teammates":
            return httpx.Response(
                200,
                json={
                    "teammates": [
                        {
                            "agent": {"id": "agent_polly", "name": "polly"},
                            "primary_conversation_id": "polly_primary",
                        }
                    ]
                },
            )
        if path == "/v1/sessions":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "polly_a2a",
                            "labels": {"omnigent.teammate.channel": "a2a"},
                        }
                    ]
                },
            )
        if path == "/v1/sessions/polly_primary":
            return httpx.Response(
                200,
                json={"host_id": "host_1", "workspace": "C:/workspaces/polly"},
            )
        if path == "/v1/sessions/polly_a2a":
            return httpx.Response(
                200,
                json={
                    "runner_id": "runner_1",
                    "host_id": "host_1",
                    "workspace": "C:/workspaces/polly",
                    "runner_online": True,
                },
            )
        if path == "/v1/sessions/polly_a2a/events":
            return httpx.Response(202, json={"queued": False})
        if path == "/v1/sessions/debby_primary/items":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": "message",
                            "data": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_file",
                                        "file_id": "source_file",
                                        "filename": "canary.md",
                                    },
                                    {"type": "input_text", "text": "Send this to Polly"},
                                ],
                            },
                        }
                    ]
                },
            )
        if path == "/v1/sessions/debby_primary/resources/files/source_file":
            return httpx.Response(
                200,
                json={"id": "source_file", "name": "canary.md"},
            )
        if path == "/v1/sessions/debby_primary/resources/files/source_file/content":
            return httpx.Response(
                200,
                content=b"A2A_FILE_CANARY",
                headers={"content-type": "text/markdown; charset=utf-8"},
            )
        if path == "/v1/sessions/polly_a2a/resources/files":
            uploaded_body = request.content
            return httpx.Response(201, json={"id": "target_file"})
        if path == "/v1/coordination/messages":
            assert isinstance(body, dict)
            coordination_body = body
            return httpx.Response(
                200,
                json={"message": {"message_id": "msg_1"}, "delivery_state": "pending"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {"teammate": "polly", "task": "Read the attached file", "wait": False},
                server_client=client,
                conversation_id="debby_primary",
                agent_spec=SimpleNamespace(name="debby"),
            )
        )

    assert result["status"] == "dispatched"
    assert result["attachment_count"] == 1
    assert result["target_file_ids"] == ["target_file"]
    assert b"A2A_FILE_CANARY" in uploaded_body
    assert b'filename="canary.md"' in uploaded_body
    assert coordination_body is not None
    assert coordination_body["artifacts"] == [
        {"type": "input_file", "file_id": "target_file", "filename": "canary.md"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["succeeded", "failed", None])
async def test_wait_uses_only_correlated_durable_result(outcome, monkeypatch) -> None:
    from omnigent.runner import tool_dispatch

    clock = [0.0]
    paths = []

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(tool_dispatch, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(tool_dispatch.asyncio, "sleep", sleep)

    def handler(request):
        path = request.url.path
        paths.append(path)
        if path == "/v1/teammates":
            return httpx.Response(
                200,
                json={
                    "teammates": [
                        {
                            "agent": {"id": "agent_polly", "name": "polly"},
                            "bot": {
                                "id": "bot_polly",
                                "host_id": "host_1",
                                "home_path": "C:/polly",
                            },
                        }
                    ]
                },
            )
        if path == "/v1/sessions":
            return httpx.Response(200, json={"data": [{"id": "polly_a2a", "purpose": "a2a"}]})
        if path == "/v1/sessions/debby_primary":
            return httpx.Response(200, json={"purpose": "primary"})
        if path == "/v1/sessions/polly_a2a":
            return httpx.Response(
                200, json={"host_id": "host_1", "runner_id": "r1", "status": "running"}
            )
        if path.endswith("/events"):
            return httpx.Response(202, json={})
        if path == "/v1/coordination/messages":
            return httpx.Response(200, json={"message": {"message_id": "msg_request"}})
        if path == "/v1/coordination/messages/msg_request":
            result = (
                None
                if outcome is None
                else {
                    "message_id": "msg_result",
                    "in_reply_to": "msg_request",
                    "payload": {"outcome": outcome, "summary": "The final verdict"},
                }
            )
            return httpx.Response(
                200, json={"message": {"message_state": "active"}, "result": result}
            )
        # Reading a shared session transcript/child list is a regression.
        raise AssertionError(path)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {
                    "teammate": "polly",
                    "task": "Review",
                    "file_ids": [],
                    "wait": True,
                    "timeout_seconds": 3,
                },
                server_client=client,
                conversation_id="debby_primary",
                agent_spec=SimpleNamespace(name="debby"),
            )
        )
    assert (
        result["status"]
        == {"succeeded": "completed", "failed": "failed", None: "in_progress"}[outcome]
    )
    assert clock[0] <= 3
    if outcome is None:
        assert "response" not in result
    assert not any(path.endswith(("/items", "/child_sessions")) for path in paths)


@pytest.mark.asyncio
async def test_reply_requires_request_id_instead_of_guessing_latest_topic() -> None:
    def handler(request):
        if request.url.path == "/v1/teammates":
            return httpx.Response(
                200,
                json={
                    "teammates": [
                        {
                            "agent": {"id": "agent_debby", "name": "debby"},
                            "bot": {"id": "bot_debby", "host_id": "host", "home_path": "C:/debby"},
                        }
                    ]
                },
            )
        raise AssertionError(request.url.path)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {"teammate": "debby", "task": "Report", "intent": "task.result"},
                server_client=client,
                conversation_id="polly_a2a",
                agent_spec=SimpleNamespace(name="polly"),
            )
        )
    assert "in_reply_to" in result["error"]


@pytest.mark.asyncio
async def test_drain_inbox_anti_busy_wait_guard() -> None:
    import asyncio

    from omnigent.runner.tool_dispatch import _drain_inbox

    empty_queue: asyncio.Queue = asyncio.Queue()
    conv = "test_conv_spin_guard"

    # First drain: regular empty notice
    res1 = await _drain_inbox(empty_queue, conversation_id=conv)
    assert "Inbox is empty" in res1
    assert "You MUST END YOUR TURN" not in res1

    # Second consecutive empty drain in the same turn: intercept directive!
    res2 = await _drain_inbox(empty_queue, conversation_id=conv)
    assert "You MUST END YOUR TURN now and stop polling" in res2
