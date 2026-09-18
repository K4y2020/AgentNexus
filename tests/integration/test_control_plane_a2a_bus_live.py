"""Live server -> runner -> harness A2A bus tests (mock LLM).

These tests drive the real ``live_server``/``live_runner_id`` stack plus the
wrapped SDK harness instead of a fake RunnerRouter. The coordination message
goes through the durable outbox -> Dispatcher -> ``POST /v1/sessions/{id}/events``
-> runner -> harness -> terminal-idle receipt, so a green result means the A2A
path is wired beyond the server unit boundary.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest

from agentnexus.runner.identity import AGENTNEXUS_INTERNAL_WS_ORIGIN
from tests.e2e.conftest import (
    configure_mock_llm,
    create_runner_bound_session,
    get_mock_requests,
    lookup_agent_id,
    register_inline_agent,
    set_fallback_mock_llm,
)

_TIMEOUT_S = 120.0


def _wait_until(predicate: Any, *, label: str, timeout_s: float = _TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
        except Exception as exc:  # surface the last failure
            last = exc
        if last:
            return last
        time.sleep(1.0)
    raise AssertionError(f"{label} not satisfied before timeout; last={last!r}")


def _create_child_session(
    client: httpx.Client,
    *,
    agent_id: str,
    parent_session_id: str,
    title: str,
) -> str:
    resp = client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "parent_session_id": parent_session_id,
            "title": title,
        },
        headers={"Origin": AGENTNEXUS_INTERNAL_WS_ORIGIN},
    )
    resp.raise_for_status()
    return str(resp.json()["id"])


def _register_agent(
    client: httpx.Client,
    *,
    suffix: str,
    harness: str,
    model: str,
    mock_llm_base_url: str | None,
    provider_name: str | None = None,
    prompt: str,
) -> str:
    return register_inline_agent(
        client,
        name=f"a2a-{suffix}-{uuid.uuid4().hex[:6]}",
        harness=harness,
        model=model,
        profile="",
        prompt=prompt,
        mock_llm_base_url=mock_llm_base_url,
        provider_name=provider_name,
    )


def _seed_tree(
    client: httpx.Client,
    *,
    harness: str,
    model: str,
    mock_llm_base_url: str | None,
    runner_id: str,
) -> tuple[str, str, str, str]:
    root_agent = _register_agent(
        client,
        suffix="root",
        harness=harness,
        model=model,
        mock_llm_base_url=mock_llm_base_url,
        prompt="You are the orchestrator.",
    )
    planner_agent = _register_agent(
        client,
        suffix="planner",
        harness=harness,
        model=model,
        mock_llm_base_url=mock_llm_base_url,
        prompt="You are the planner.",
    )
    implementer_agent = _register_agent(
        client,
        suffix="implementer",
        harness=harness,
        model=model,
        mock_llm_base_url=mock_llm_base_url,
        prompt="You are the implementer.",
    )
    reviewer_agent = _register_agent(
        client,
        suffix="reviewer",
        harness=harness,
        model=model,
        mock_llm_base_url=mock_llm_base_url,
        prompt="You are the reviewer.",
    )
    root_id = create_runner_bound_session(
        client,
        agent_name=root_agent,
        runner_id=runner_id,
    )
    planner_id = _create_child_session(
        client,
        agent_id=lookup_agent_id(client, planner_agent),
        parent_session_id=root_id,
        title="a2a-planner",
    )
    implementer_id = _create_child_session(
        client,
        agent_id=lookup_agent_id(client, implementer_agent),
        parent_session_id=root_id,
        title="a2a-implementer",
    )
    reviewer_id = _create_child_session(
        client,
        agent_id=lookup_agent_id(client, reviewer_agent),
        parent_session_id=root_id,
        title="a2a-reviewer",
    )
    return root_id, planner_id, implementer_id, reviewer_id


def _mock_base_for_harness(
    mock_llm_server_url: str | None,
    *,
    harness: str,
) -> str | None:
    """Return the auth base URL an executor needs for the mock LLM server.

    Claude's Anthropic SDK appends ``/v1/messages`` itself, while OpenAI
    Responses SDKs expect a base URL that ends in ``/v1``.
    """
    if mock_llm_server_url is None:
        return None
    if harness == "claude-sdk":
        return mock_llm_server_url
    return f"{mock_llm_server_url}/v1"


def _seed_mixed_harness_tree(
    client: httpx.Client,
    *,
    root_harness: str,
    root_model: str,
    root_prompt: str,
    planner_harness: str,
    planner_model: str,
    implementer_harness: str,
    implementer_model: str,
    reviewer_harness: str,
    reviewer_model: str,
    mock_llm_server_url: str | None,
    runner_id: str,
    implementer_provider_name: str | None = None,
) -> tuple[str, str, str, str]:
    """Register a root + three children where each role uses its own harness."""
    root_agent = _register_agent(
        client,
        suffix="root",
        harness=root_harness,
        model=root_model,
        mock_llm_base_url=_mock_base_for_harness(mock_llm_server_url, harness=root_harness),
        prompt=root_prompt,
    )
    planner_agent = _register_agent(
        client,
        suffix="planner",
        harness=planner_harness,
        model=planner_model,
        mock_llm_base_url=_mock_base_for_harness(mock_llm_server_url, harness=planner_harness),
        prompt="You are the planner.",
    )
    implementer_agent = _register_agent(
        client,
        suffix="implementer",
        harness=implementer_harness,
        model=implementer_model,
        mock_llm_base_url=_mock_base_for_harness(mock_llm_server_url, harness=implementer_harness),
        provider_name=implementer_provider_name,
        prompt="You are the implementer.",
    )
    reviewer_agent = _register_agent(
        client,
        suffix="reviewer",
        harness=reviewer_harness,
        model=reviewer_model,
        mock_llm_base_url=_mock_base_for_harness(mock_llm_server_url, harness=reviewer_harness),
        prompt="You are the reviewer.",
    )
    root_id = create_runner_bound_session(
        client,
        agent_name=root_agent,
        runner_id=runner_id,
    )
    planner_id = _create_child_session(
        client,
        agent_id=lookup_agent_id(client, planner_agent),
        parent_session_id=root_id,
        title="a2a-planner",
    )
    implementer_id = _create_child_session(
        client,
        agent_id=lookup_agent_id(client, implementer_agent),
        parent_session_id=root_id,
        title="a2a-implementer",
    )
    reviewer_id = _create_child_session(
        client,
        agent_id=lookup_agent_id(client, reviewer_agent),
        parent_session_id=root_id,
        title="a2a-reviewer",
    )
    return root_id, planner_id, implementer_id, reviewer_id


def _message_digest(
    client: httpx.Client,
    *,
    root_session_id: str,
    recipient_session_id: str,
) -> dict[str, dict[str, Any]]:
    resp = client.get(
        "/v1/coordination/messages",
        params={
            "root_session_id": root_session_id,
            "recipient_session_id": recipient_session_id,
        },
    )
    resp.raise_for_status()
    return {m["message_id"]: m for m in resp.json()["messages"]}


def test_live_a2a_message_reaches_harness_and_receives_consumed(
    live_server: str,
    live_runner_id: str,
    harness_name: str,
    model_name: str,
    mock_llm_server_url: str | None,
    request: pytest.FixtureRequest,
) -> None:
    """Dispatcher -> runner -> harness -> terminal-idle receipt, no fake router."""
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": "Received the task."}],
    )
    with httpx.Client(base_url=live_server, timeout=300) as client:
        root_id, planner_id, _impl, _rev = _seed_tree(
            client,
            harness=harness_name,
            model=model_name,
            mock_llm_base_url=_mock_base_for_harness(mock_llm_server_url, harness=harness_name),
            runner_id=live_runner_id,
        )
        token = f"A2A-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/v1/coordination/messages",
            json={
                "root_session_id": root_id,
                "sender_session_id": root_id,
                "sender_role": "user_orchestrator",
                "recipient_session_id": planner_id,
                "recipient_role": "planner",
                "intent": "task.request",
                "payload": {"prompt": f"Analyze {token}."},
            },
        )
        resp.raise_for_status()
        message_id = str(resp.json()["message"]["message_id"])

        def _received() -> dict[str, Any] | None:
            digest = _message_digest(
                client,
                root_session_id=root_id,
                recipient_session_id=planner_id,
            )
            msg = digest.get(message_id)
            if msg and msg["consumption_state"] == "consumed":
                return msg
            return None

        final = _wait_until(
            _received,
            label="live A2A message consumed by harness turn",
            timeout_s=_TIMEOUT_S,
        )
        assert final is not None
        assert final["message_state"] == "active"
        print(f"live A2A message {message_id} delivered to {planner_id} and consumed")


def test_live_declared_a2a_result_returns_to_origin(
    live_server: str,
    live_runner_id: str,
    harness_name: str,
    model_name: str,
    mock_llm_server_url: str | None,
) -> None:
    """A progress turn cannot finish a task; its later final declaration returns automatically."""
    token = uuid.uuid4().hex
    progress_token = f"progress-{token}"
    complete_token = f"complete-{token}"
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": "Working; final report is pending."}],
        match=progress_token,
    )
    # SDK preflight/replay requests must not drain another stage's scripted answer.
    set_fallback_mock_llm(mock_llm_server_url, progress_token, "Working; final report is pending.")
    with httpx.Client(base_url=live_server, timeout=300) as client:
        root, target, _, _ = _seed_tree(
            client,
            harness=harness_name,
            model=model_name,
            mock_llm_base_url=_mock_base_for_harness(mock_llm_server_url, harness=harness_name),
            runner_id=live_runner_id,
        )
        sent = client.post(
            "/v1/coordination/messages",
            json={
                "root_session_id": root,
                "sender_session_id": root,
                "sender_role": "user_orchestrator",
                "recipient_session_id": target,
                "recipient_role": "planner",
                "kind": "command",
                "intent": "task.request",
                "payload": {
                    "prompt": f"Review {progress_token} and report your final conclusion."
                },
            },
        )
        sent.raise_for_status()
        request_id = sent.json()["message"]["message_id"]

        def snapshot():
            response = client.get(f"/v1/coordination/messages/{request_id}")
            response.raise_for_status()
            return response.json()

        _wait_until(
            lambda: snapshot()["message"]["consumption_state"] == "consumed", label="progress turn"
        )
        assert snapshot()["result"] is None
        configure_mock_llm(
            mock_llm_server_url,
            [{"text": f"Final verified verdict.\n[A2A_RESULT:{request_id}:succeeded]"}],
            match=complete_token,
        )
        configure_mock_llm(
            mock_llm_server_url,
            [{"text": f"The final verdict for {request_id} is ready for the user."}],
            match=f"Final result for request {request_id}",
        )
        set_fallback_mock_llm(
            mock_llm_server_url, complete_token,
            f"Final verified verdict.\n[A2A_RESULT:{request_id}:succeeded]",
        )
        set_fallback_mock_llm(
            mock_llm_server_url, f"Final result for request {request_id}",
            f"The final verdict for {request_id} is ready for the user.",
        )
        response = client.post(
            "/v1/coordination/messages",
            json={
                "root_session_id": root,
                "sender_session_id": root,
                "sender_role": "user_orchestrator",
                "recipient_session_id": target,
                "payload": {"prompt": f"Finish the existing review now: {complete_token}"},
            },
        )
        assert response.status_code == 200, response.text
        final = _wait_until(lambda: snapshot()["result"], label="durable A2A result")
        assert final["recipient_session_id"] == root
        assert final["in_reply_to"] == request_id
        assert final["payload"]["summary"] == "Final verified verdict."

        def returned():
            items = client.get(
                f"/v1/sessions/{root}/items", params={"order": "desc", "limit": 50}
            ).json()["data"]
            delivered = client.get(f"/v1/coordination/messages/{final['message_id']}").json()
            return delivered["message"]["consumption_state"] == "consumed" and any(
                item.get("role") == "assistant"
                and any(
                    block.get("text")
                    == f"The final verdict for {request_id} is ready for the user."
                    for block in item.get("content", [])
                )
                for item in items
            )

        _wait_until(returned, label="result injected into originating conversation")


def test_live_plan_implement_review_advances_through_harness_turns(
    live_server: str,
    live_runner_id: str,
    harness_name: str,
    model_name: str,
    mock_llm_server_url: str | None,
    request: pytest.FixtureRequest,
) -> None:
    """One workflow run advances through four real harness/mock-LLM turns."""
    configure_mock_llm(
        mock_llm_server_url,
        [
            {"text": "Plan ready. [WORKFLOW_RESULT: succeeded]"},
            {"text": "Implementation complete. [WORKFLOW_RESULT: succeeded]"},
            {"text": "Approved. [REVIEW_DECISION: approved]"},
            {"text": "All tests pass. [WORKFLOW_RESULT: succeeded]"},
        ],
    )
    with httpx.Client(base_url=live_server, timeout=300) as client:
        root_id, planner_id, implementer_id, reviewer_id = _seed_tree(
            client,
            harness=harness_name,
            model=model_name,
            mock_llm_base_url=_mock_base_for_harness(mock_llm_server_url, harness=harness_name),
            runner_id=live_runner_id,
        )
        token = f"WF-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/v1/coordination/workflows/plan-implement-review",
            json={
                "title": "Live A2A Workflow",
                "root_session_id": root_id,
                "planner_session_id": planner_id,
                "implementer_session_id": implementer_id,
                "reviewer_session_id": reviewer_id,
                "user_prompt": f"Build a tiny mock project. {token}",
                "workspace_path": ".",
                "budget": {"task_deadline_s": 90},
            },
        )
        resp.raise_for_status()
        run_id = str(resp.json()["run"]["run_id"])

        def _summary() -> dict[str, Any]:
            summary_resp = client.get(f"/v1/coordination/runs/{run_id}/summary")
            summary_resp.raise_for_status()
            return summary_resp.json()

        def _succeeded() -> dict[str, Any] | None:
            body = _summary()
            if body["run"]["status"] == "succeeded":
                return body
            return None

        final = _wait_until(
            _succeeded,
            label="workflow advanced through all harness turns",
            timeout_s=_TIMEOUT_S,
        )
        assert final is not None
        stages = final["summary"]["stage"]
        assert stages["planner"] == "succeeded"
        assert stages["implementer"] == "succeeded"
        assert stages["reviewer"] == "succeeded"
        assert stages["tester"] == "succeeded"
        assert final["summary"]["consumption_states"].get("consumed", 0) >= 4
        print(f"live workflow {run_id} succeeded after {final['summary']['consumption_states']}")


def test_live_a2a_workflow_crosses_harness_families(
    live_server: str,
    live_runner_id: str,
    mock_llm_server_url: str | None,
    request: pytest.FixtureRequest,
) -> None:
    """Claude planner -> Codex implementer -> Claude reviewer/test turn loop.

    Unlike the single-harness workflow test, each stage binds a different
    harness family. The mock-LLM capture proves the Claude turns really used
    ``/v1/messages`` and the Codex turn really used the OpenAI Responses
    ``/v1/responses`` wire instead of every stage silently sharing one SDK.
    """
    if mock_llm_server_url is None:
        pytest.skip("cross-harness A2A requires the mock LLM server")
    planner_model = f"claude-planner-{uuid.uuid4().hex[:8]}"
    implementer_model = "gpt-5.6-sol"
    reviewer_model = f"claude-reviewer-{uuid.uuid4().hex[:8]}"
    root_model = f"openai-root-{uuid.uuid4().hex[:8]}"

    # The Claude Agent SDK calls its model once for auto-title generation
    # before the actual turn, using the same model key. Keep a slot ahead of
    # every real marker so the workflow sees the controlled stage outcome.
    configure_mock_llm(
        mock_llm_server_url,
        [
            {"text": "Planning session"},
            {"text": "Plan ready. [WORKFLOW_RESULT: succeeded]"},
        ],
        key=planner_model,
    )
    configure_mock_llm(
        mock_llm_server_url,
        [
            {"text": "Implementation complete. [WORKFLOW_RESULT: succeeded]"},
        ],
        key=implementer_model,
    )
    set_fallback_mock_llm(
        mock_llm_server_url,
        implementer_model,
        "Implementation complete. [WORKFLOW_RESULT: succeeded]",
    )
    configure_mock_llm(
        mock_llm_server_url,
        [
            {"text": "Review session"},
            {"text": "Approved. [REVIEW_DECISION: approved]"},
            {"text": "Test session"},
            {"text": "All tests pass. [WORKFLOW_RESULT: succeeded]"},
        ],
        key=reviewer_model,
    )

    with httpx.Client(base_url=live_server, timeout=300) as client:
        root_id, planner_id, implementer_id, reviewer_id = _seed_mixed_harness_tree(
            client,
            root_harness="openai-agents",
            root_model=root_model,
            root_prompt="You are the orchestrator.",
            planner_harness="claude-sdk",
            planner_model=planner_model,
            implementer_harness="codex",
            implementer_model=implementer_model,
            implementer_provider_name="mock-openai",
            reviewer_harness="claude-sdk",
            reviewer_model=reviewer_model,
            mock_llm_server_url=mock_llm_server_url,
            runner_id=live_runner_id,
        )
        token = f"XFAM-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/v1/coordination/workflows/plan-implement-review",
            json={
                "title": "Live Cross-Harness Workflow",
                "root_session_id": root_id,
                "planner_session_id": planner_id,
                "implementer_session_id": implementer_id,
                "reviewer_session_id": reviewer_id,
                "user_prompt": f"Build a tiny mock project. {token}",
                "workspace_path": ".",
                "budget": {"task_deadline_s": 150},
            },
        )
        resp.raise_for_status()
        run_id = str(resp.json()["run"]["run_id"])

        def _summary() -> dict[str, Any]:
            summary_resp = client.get(f"/v1/coordination/runs/{run_id}/summary")
            summary_resp.raise_for_status()
            return summary_resp.json()

        def _succeeded() -> dict[str, Any] | None:
            body = _summary()
            if body["run"]["status"] == "succeeded":
                return body
            return None

        final = _wait_until(
            _succeeded,
            label="cross-harness workflow advanced through all turns",
            timeout_s=180.0,
        )
        assert final is not None
        stages = final["summary"]["stage"]
        assert stages["planner"] == "succeeded"
        assert stages["implementer"] == "succeeded"
        assert stages["reviewer"] == "succeeded"
        assert stages["tester"] == "succeeded"
        assert final["summary"]["consumption_states"].get("consumed", 0) >= 4

        claude_requests = get_mock_requests(mock_llm_server_url, key=planner_model)
        codex_requests = get_mock_requests(mock_llm_server_url, key=implementer_model)
        reviewer_requests = get_mock_requests(mock_llm_server_url, key=reviewer_model)
        assert claude_requests, "planner never reached the Anthropic mock endpoint"
        assert codex_requests, "implementer never reached the OpenAI mock endpoint"
        assert reviewer_requests, "reviewer never reached the Anthropic mock endpoint"
        assert all("messages" in req for req in claude_requests), (
            f"planner requests were not Anthropic Messages: {claude_requests}"
        )
        assert all("messages" in req for req in reviewer_requests), (
            f"reviewer requests were not Anthropic Messages: {reviewer_requests}"
        )
        assert all("input" in req or "stream" in req for req in codex_requests), (
            f"implementer requests were not OpenAI Responses: {codex_requests}"
        )
        print(
            f"cross-harness workflow {run_id} consumed "
            f"{final['summary']['consumption_states']} with claude/codex/claude"
        )
