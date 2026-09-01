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

from omnigent.runner.identity import OMNIGENT_INTERNAL_WS_ORIGIN
from tests.e2e.conftest import (
    configure_mock_llm,
    create_runner_bound_session,
    lookup_agent_id,
    register_inline_agent,
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
        headers={"Origin": OMNIGENT_INTERNAL_WS_ORIGIN},
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
            mock_llm_base_url=(
                f"{mock_llm_server_url}/v1" if mock_llm_server_url else None
            ),
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
        print(
            f"live A2A message {message_id} delivered to {planner_id} and consumed"
        )


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
            mock_llm_base_url=(
                f"{mock_llm_server_url}/v1" if mock_llm_server_url else None
            ),
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
        print(
            f"live workflow {run_id} succeeded after "
            f"{final['summary']['consumption_states']}"
        )