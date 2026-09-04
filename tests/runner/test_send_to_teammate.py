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
async def test_send_to_teammate_waits_for_completion_and_returns_response() -> None:
    session_polls = 0
    items_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_polls, items_polls
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
                                "default_model": "gpt-5.6-luna",
                            },
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
                            "purpose": "a2a",
                            "labels": {"omnigent.teammate.channel": "a2a"},
                        }
                    ]
                },
            )
        if path == "/v1/sessions/polly_primary":
            return httpx.Response(
                200, json={"host_id": "host_1", "workspace": "C:/workspaces/polly"}
            )
        if path == "/v1/sessions/polly_a2a":
            session_polls += 1
            payload = {
                "runner_id": "r1",
                "host_id": "host_1",
                "runner_online": True,
                "workspace": "C:/workspaces/polly",
            }
            if session_polls == 1:
                # Discovery snapshot
                return httpx.Response(200, json={"status": "idle", **payload})
            if session_polls == 2:
                # First wait poll
                return httpx.Response(200, json={"status": "running", **payload})
            # Second wait poll -> finished
            return httpx.Response(200, json={"status": "idle", **payload})
        if path == "/v1/sessions/polly_a2a/events":
            return httpx.Response(202, json={"queued": False})
        if path == "/v1/sessions/debby_primary/items":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/sessions/polly_a2a/items":
            items_polls += 1
            if items_polls == 1:
                # Before dispatch
                return httpx.Response(200, json={"data": [{"id": "item_old"}]})
            # After dispatch completion
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "item_new_assistant",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "Task finished successfully."}
                            ],
                        },
                        {"id": "item_old"},
                    ]
                },
            )
        if path == "/v1/coordination/messages":
            return httpx.Response(
                200, json={"message": {"message_id": "msg_wait_1"}, "delivery_state": "pending"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {"teammate": "polly", "task": "Check report", "wait": True, "timeout_seconds": 5},
                server_client=client,
                conversation_id="debby_primary",
                agent_spec=SimpleNamespace(name="debby"),
            )
        )

    assert result["status"] == "completed"
    assert result["target_teammate"] == "polly"
    assert result["response"] == "Task finished successfully."
    assert result["session_url"] == "/c/polly_a2a"
