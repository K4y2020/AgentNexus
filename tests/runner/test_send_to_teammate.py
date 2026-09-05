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


@pytest.mark.asyncio
async def test_send_to_teammate_waits_for_child_subagents_before_completing() -> None:
    session_polls = 0
    children_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_polls, children_polls
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
                            "purpose": "a2a",
                            "labels": {"omnigent.teammate.channel": "a2a"},
                        }
                    ]
                },
            )
        if path == "/v1/sessions/debby_primary":
            return httpx.Response(200, json={"host_id": "host_1", "workspace": "C:/debby"})
        if path == "/v1/sessions/polly_a2a":
            session_polls += 1
            payload = {
                "runner_id": "r1",
                "host_id": "host_1",
                "runner_online": True,
                "workspace": "C:/polly",
            }
            if session_polls == 1:
                return httpx.Response(200, json={"status": "idle", **payload})
            if session_polls == 2:
                # Turn 1 running (dispatching subagents)
                return httpx.Response(200, json={"status": "running", **payload})
            # Turn 1 ended, subagents running in background
            return httpx.Response(200, json={"status": "idle", **payload})
        if path == "/v1/sessions/polly_a2a/events":
            return httpx.Response(202, json={"queued": False})
        if path == "/v1/sessions/polly_a2a/child_sessions":
            children_polls += 1
            if children_polls == 1:
                # Sub-agents are still busy!
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "c1", "busy": True, "current_task_status": "running"},
                            {"id": "c2", "busy": True, "current_task_status": "running"},
                        ]
                    },
                )
            # Sub-agents completed
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "c1", "busy": False, "current_task_status": "completed"},
                        {"id": "c2", "busy": False, "current_task_status": "completed"},
                    ]
                },
            )
        if path == "/v1/sessions/debby_primary/items":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/sessions/polly_a2a/items":
            if session_polls <= 1:
                return httpx.Response(200, json={"data": [{"id": "item_old"}]})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "item_final",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Comprehensive Subagent Synthesis Report",
                                }
                            ],
                        },
                        {"id": "item_old"},
                    ]
                },
            )
        if path == "/v1/coordination/messages":
            return httpx.Response(
                200, json={"message": {"message_id": "msg_child_1"}, "delivery_state": "pending"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {
                    "teammate": "polly",
                    "task": "Explore repo with subagents",
                    "wait": True,
                    "timeout_seconds": 10,
                },
                server_client=client,
                conversation_id="debby_primary",
                agent_spec=SimpleNamespace(name="debby"),
            )
        )

    assert result["status"] == "completed"
    assert result["response"] == "Comprehensive Subagent Synthesis Report"
    assert children_polls >= 2


@pytest.mark.asyncio
async def test_send_to_teammate_reverse_routing_to_originating_session() -> None:
    coordination_recipient = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal coordination_recipient
        path = request.url.path
        if path == "/v1/teammates":
            return httpx.Response(
                200,
                json={
                    "teammates": [
                        {
                            "agent": {"id": "agent_debby", "name": "debby"},
                            "primary_conversation_id": "debby_primary",
                        }
                    ]
                },
            )
        if path == "/v1/sessions/polly_a2a":
            return httpx.Response(
                200,
                json={
                    "id": "polly_a2a",
                    "purpose": "a2a",
                    "host_id": "host_1",
                    "workspace": "C:/polly",
                    "labels": {"omnigent.teammate.channel": "a2a"},
                },
            )
        if path == "/v1/sessions/polly_a2a/items":
            # Polly A2A session received a task from debby in conv_user_topic_999
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "item_a2a_ask",
                            "metadata": {
                                "a2a": True,
                                "sender_role": "debby",
                                "sender_session_id": "conv_user_topic_999",
                            },
                        }
                    ]
                },
            )
        if path == "/v1/sessions/conv_user_topic_999":
            return httpx.Response(
                200,
                json={"id": "conv_user_topic_999", "host_id": "host_1", "workspace": "C:/debby"},
            )
        if path == "/v1/sessions/conv_user_topic_999/events":
            return httpx.Response(202, json={"queued": False})
        if path == "/v1/coordination/messages":
            body = json.loads(request.content)
            coordination_recipient = body.get("recipient_session_id")
            return httpx.Response(
                200, json={"message": {"message_id": "msg_rev_1"}, "delivery_state": "pending"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {"teammate": "debby", "task": "Here is the final report", "wait": False},
                server_client=client,
                conversation_id="polly_a2a",
                agent_spec=SimpleNamespace(name="polly"),
            )
        )

    assert result["status"] == "dispatched"
    assert result["target_session_id"] == "conv_user_topic_999"
    assert coordination_recipient == "conv_user_topic_999"


@pytest.mark.asyncio
async def test_send_to_teammate_dual_output_extraction_from_task_payload() -> None:
    dual_polls = 0
    items_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal dual_polls, items_count
        path = request.url.path
        if path == "/v1/teammates":
            return httpx.Response(
                200,
                json={"teammates": [{"agent": {"id": "agent_polly", "name": "polly"}}]},
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
        if path == "/v1/sessions/debby_primary":
            return httpx.Response(200, json={"host_id": "host_1", "workspace": "C:/debby"})
        if path == "/v1/sessions/polly_a2a":
            dual_polls += 1
            return httpx.Response(
                200,
                json={
                    "status": "running" if dual_polls == 2 else "idle",
                    "runner_id": "r1",
                    "host_id": "host_1",
                    "runner_online": True,
                    "workspace": "C:/polly",
                },
            )
        if path == "/v1/sessions/polly_a2a/events":
            return httpx.Response(202, json={"queued": False})
        if path == "/v1/sessions/polly_a2a/child_sessions":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/sessions/debby_primary/items":
            return httpx.Response(200, json={"data": []})
        if path == "/v1/sessions/polly_a2a/items":
            items_count += 1
            if items_count == 1:
                return httpx.Response(200, json={"data": [{"id": "item_old"}]})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "item_msg",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Report sent to Debby."}],
                        },
                        {
                            "id": "item_tool",
                            "name": "send_to_teammate",
                            "arguments": json.dumps(
                                {
                                    "teammate": "debby",
                                    "task": "# Full 500-Line Deep Architectural Audit Report",
                                }
                            ),
                        },
                        {"id": "item_old"},
                    ]
                },
            )
        if path == "/v1/coordination/messages":
            return httpx.Response(
                200, json={"message": {"message_id": "msg_dual_1"}, "delivery_state": "pending"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://server"
    ) as client:
        result = json.loads(
            await _execute_send_to_teammate_tool(
                {"teammate": "polly", "task": "Do audit", "wait": True, "timeout_seconds": 5},
                server_client=client,
                conversation_id="debby_primary",
                agent_spec=SimpleNamespace(name="debby"),
            )
        )

    assert result["status"] == "completed"
    assert result["response"] == "# Full 500-Line Deep Architectural Audit Report"


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
