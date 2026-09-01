"""Repeatable control-plane reliability run samples (mock LLM).

Each parametrized sample boots one full Plan -> Implement -> Review -> Test
workflow through the durable outbox, real server lifespan, runner, wrapped SDK
harness, and terminal-idle receipt. In CI the default is one sample so the
normal integration shard stays cheap; the P6 baseline sets
``AGENTNEXUS_RELIABILITY_RUNS=100`` to collect an independent 100-run sample.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import pytest

from tests.e2e.conftest import configure_mock_llm
from tests.integration.test_control_plane_a2a_bus_live import (
    _mock_base_for_harness,
    _seed_tree,
)

RELIABILITY_RUNS = max(
    1,
    int(os.environ.get("AGENTNEXUS_RELIABILITY_RUNS", "1")),
)
_TIMEOUT_S = 180.0


@pytest.mark.parametrize("run_index", range(RELIABILITY_RUNS))
def test_control_plane_reliability_workflow_run(
    run_index: int,
    live_server: str,
    live_runner_id: str,
    harness_name: str,
    model_name: str,
    mock_llm_server_url: str | None,
) -> None:
    """One independent reliability run must complete without effect_unknown."""
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
        token = f"RL-{run_index}-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/v1/coordination/workflows/plan-implement-review",
            json={
                "title": "Reliability Run",
                "root_session_id": root_id,
                "planner_session_id": planner_id,
                "implementer_session_id": implementer_id,
                "reviewer_session_id": reviewer_id,
                "user_prompt": f"Build a tiny mock project. {token}",
                "workspace_path": ".",
                "budget": {"task_deadline_s": 120},
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

        final = _wait_until_succeeded(_succeeded, timeout_s=_TIMEOUT_S)
        assert final is not None
        stages = final["summary"]["stage"]
        assert stages["planner"] == "succeeded"
        assert stages["implementer"] == "succeeded"
        assert stages["reviewer"] == "succeeded"
        assert stages["tester"] == "succeeded"
        assert final["summary"]["consumption_states"].get("consumed", 0) >= 4
        assert final["summary"]["effect_unknown_count"] == 0
        print(
            f"reliability run {run_index} ({run_id}) succeeded "
            f"after {final['summary']['consumption_states']}"
        )


def _wait_until_succeeded(
    predicate: Any,
    *,
    timeout_s: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
        except Exception as exc:
            last = exc
        if last:
            return last
        time.sleep(1.0)
    raise AssertionError(f"workflow did not succeed before timeout; last={last!r}")
