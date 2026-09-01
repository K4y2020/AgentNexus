"""Operator-gated real-provider control-plane workflow acceptance.

Unlike the mock-LLM A2A tests, agents here are registered with no
``mock_llm_base_url`` and no provider auth block. The Codex executor
therefore bridges the operator's real ``~/.codex`` login/config into each
per-session CODEX_HOME, and every workflow stage runs a real LLM turn with
real delivery receipts.

This test is deliberately opt-in: set ``OMNIGENT_REAL_PROVIDER_E2E=1`` and
pass ``--llm-api-key`` so the e2e fixture boots in real-LLM mode. The key
value is not a secret requirement; Codex reads its own config.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import pytest

from tests.e2e.conftest import (
    create_runner_bound_session,
    lookup_agent_id,
    register_inline_agent,
)

_REAL_PROVIDER_OPT_IN = "OMNIGENT_REAL_PROVIDER_E2E"
_REAL_PROVIDER_TIMEOUT_S = 900.0


def _wait_until(predicate: Any, *, label: str) -> Any:
    deadline = time.monotonic() + _REAL_PROVIDER_TIMEOUT_S
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
        except Exception as exc:  # surface the last failure
            last = exc
        if last:
            return last
        time.sleep(2.0)
    raise AssertionError(f"{label} not satisfied before timeout; last={last!r}")


def _register_agent(
    client: httpx.Client,
    *,
    suffix: str,
    harness: str,
    model: str,
    prompt: str,
) -> str:
    """Register an agent whose LLM auth comes from the CLI, not a mock."""
    return register_inline_agent(
        client,
        name=f"real-provider-{suffix}-{uuid.uuid4().hex[:6]}",
        harness=harness,
        model=model,
        profile="",
        prompt=prompt,
        mock_llm_base_url=None,
    )


def _create_child_session(
    client: httpx.Client,
    *,
    agent_name: str,
    parent_session_id: str,
    title: str,
) -> str:
    resp = client.post(
        "/v1/sessions",
        json={
            "agent_id": lookup_agent_id(client, agent_name),
            "parent_session_id": parent_session_id,
            "title": title,
        },
    )
    resp.raise_for_status()
    return str(resp.json()["id"])


@pytest.mark.timeout(900)
def test_real_provider_workflow_advances_through_harness_turns(
    live_server: str,
    live_runner_id: str,
    harness_name: str,
    model_name: str,
    using_mock_llm: bool,
) -> None:
    """One run reaches succeeded through real, consumed harness turns."""
    opt_in = os.environ.get(_REAL_PROVIDER_OPT_IN, "").strip().lower()
    if using_mock_llm or opt_in not in {"1", "true", "yes"}:
        pytest.skip(
            "real-provider control-plane acceptance requires "
            f"{_REAL_PROVIDER_OPT_IN}=1 and --llm-api-key"
        )

    with httpx.Client(base_url=live_server, timeout=_REAL_PROVIDER_TIMEOUT_S + 60) as client:
        root_agent = _register_agent(
            client,
            suffix="root",
            harness=harness_name,
            model=model_name,
            prompt="You are the orchestrator.",
        )
        planner_agent = _register_agent(
            client,
            suffix="planner",
            harness=harness_name,
            model=model_name,
            prompt="You are the planner.",
        )
        implementer_agent = _register_agent(
            client,
            suffix="implementer",
            harness=harness_name,
            model=model_name,
            prompt="You are the implementer.",
        )
        reviewer_agent = _register_agent(
            client,
            suffix="reviewer",
            harness=harness_name,
            model=model_name,
            prompt="You are the reviewer.",
        )

        root_id = create_runner_bound_session(
            client,
            agent_name=root_agent,
            runner_id=live_runner_id,
        )
        planner_id = _create_child_session(
            client,
            agent_name=planner_agent,
            parent_session_id=root_id,
            title="real-plan",
        )
        implementer_id = _create_child_session(
            client,
            agent_name=implementer_agent,
            parent_session_id=root_id,
            title="real-implement",
        )
        reviewer_id = _create_child_session(
            client,
            agent_name=reviewer_agent,
            parent_session_id=root_id,
            title="real-review",
        )

        resp = client.post(
            "/v1/coordination/workflows/plan-implement-review",
            json={
                "title": "Real Provider Workflow",
                "root_session_id": root_id,
                "planner_session_id": planner_id,
                "implementer_session_id": implementer_id,
                "reviewer_session_id": reviewer_id,
                "user_prompt": (
                    "Reply to every request exactly as instructed. Keep each turn terse."
                ),
                "workspace_path": ".",
                "budget": {"task_deadline_s": 900},
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
            label="real-provider workflow completed all stages",
        )
        assert final is not None
        stages = final["summary"]["stage"]
        assert stages["planner"] == "succeeded"
        assert stages["implementer"] == "succeeded"
        assert stages["reviewer"] == "succeeded"
        assert stages["tester"] == "succeeded"
        assert final["summary"]["consumption_states"].get("consumed", 0) >= 4
        print(
            f"real-provider workflow {run_id} succeeded after "
            f"{final['summary']['consumption_states']}"
        )
