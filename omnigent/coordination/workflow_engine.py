"""Multi-Agent Coordination Workflow Engine for Plan -> Implement -> Review DAGs (FLOW-001, FLOW-002)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from omnigent.coordination.store import CoordinationStore, get_default_coordination_db_path
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationEvent,
    CoordinationRun,
    CoordinationTask,
)
from omnigent.workspaces.lease import WorkspaceCoordinator

_logger = logging.getLogger(__name__)


class CoordinationWorkflowEngine:
    """Executes and coordinates structured multi-agent workflows (Plan -> Implement -> Review)."""

    def __init__(
        self,
        store: CoordinationStore | None = None,
        workspace_coord: WorkspaceCoordinator | None = None,
    ) -> None:
        self.store = store or CoordinationStore(get_default_coordination_db_path())
        self.workspace_coord = workspace_coord or WorkspaceCoordinator()

    async def start_plan_implement_review_run(
        self,
        *,
        title: str,
        root_session_id: str,
        planner_session_id: str,
        implementer_session_id: str,
        reviewer_session_id: str,
        user_prompt: str,
        workspace_path: str = ".",
    ) -> CoordinationRun:
        """Initialize and kickoff a complete 3-stage collaborative coding workflow."""
        run = CoordinationRun(
            title=title,
            root_session_id=root_session_id,
            template="plan_implement_review",
            status="running",
            metadata={
                "planner_id": planner_session_id,
                "implementer_id": implementer_session_id,
                "reviewer_id": reviewer_session_id,
                "workspace_path": workspace_path,
                "initial_prompt": user_prompt,
            },
        )
        await asyncio.to_thread(self.store.create_run, run)

        # 1. Create Task DAG
        t_plan = CoordinationTask(
            run_id=run.run_id,
            title="Stage 1: Architecture & Task Planning",
            status="assigned",
            assignee_session_id=planner_session_id,
            assignee_role="planner",
        )
        t_impl = CoordinationTask(
            run_id=run.run_id,
            title="Stage 2: Implementation & Testing",
            status="queued",
            assignee_session_id=implementer_session_id,
            assignee_role="implementer",
            dependencies=[t_plan.task_id],
        )
        t_rev = CoordinationTask(
            run_id=run.run_id,
            title="Stage 3: Code Review & Verification",
            status="queued",
            assignee_session_id=reviewer_session_id,
            assignee_role="reviewer",
            dependencies=[t_impl.task_id],
        )

        for task in (t_plan, t_impl, t_rev):
            await asyncio.to_thread(self.store.create_task, task)

        # 2. Dispatch kickoff message to Planner
        kickoff_msg = AgentMessage(
            root_session_id=root_session_id,
            run_id=run.run_id,
            task_id=t_plan.task_id,
            sender_session_id=root_session_id,
            sender_role="user_orchestrator",
            recipient_session_id=planner_session_id,
            recipient_role="planner",
            intent="task.request",
            payload={
                "stage": "planning",
                "prompt": f"Please analyze and create an implementation plan for: {user_prompt}",
            },
        )
        await asyncio.to_thread(self.store.save_message_and_outbox, kickoff_msg)

        # 3. Log event
        evt = CoordinationEvent(
            root_session_id=root_session_id,
            run_id=run.run_id,
            task_id=t_plan.task_id,
            actor_session_id=root_session_id,
            event_type="workflow.started",
            payload={"run_id": run.run_id, "title": title, "prompt": user_prompt},
        )
        await asyncio.to_thread(self.store.record_event, evt)

        _logger.info("Started Plan->Implement->Review run %s for session %s", run.run_id, root_session_id)
        return run
