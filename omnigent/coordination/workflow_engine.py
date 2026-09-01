"""Resumable fixed Plan -> Implement -> Review -> Fix -> Test workflow engine.

Runs the P3 fixed workflow state machine on the durable coordination store.
Each stage owns a task row; ``advance`` is the only way stages move, so a
restart or API retry can resume from the exact task without replaying
already-succeeded stages. No generic DAG editor here — one fixed template by
design (P4 will generalize beyond it).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from omnigent.coordination.store import CoordinationStore, get_default_coordination_db_path
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationArtifact,
    CoordinationEvent,
    CoordinationRun,
    CoordinationTask,
)
from omnigent.workspaces.lease import WorkspaceCoordinator

_logger = logging.getLogger(__name__)

WorkflowOutcome = Literal["succeeded", "failed"]


class CoordinationWorkflowEngine:
    """Executes and coordinates the fixed five-stage coding workflow."""

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
        """Initialize and kick off the fixed Plan -> Implement -> Review -> Fix -> Test run."""
        run = CoordinationRun(
            title=title,
            root_session_id=root_session_id,
            template="plan_implement_review_fix_test",
            status="running",
            metadata={
                "planner_id": planner_session_id,
                "implementer_id": implementer_session_id,
                "reviewer_id": reviewer_session_id,
                "workspace_path": workspace_path,
                "initial_prompt": user_prompt,
                "stage": "planning",
                "fix_cycles": 0,
            },
        )
        await asyncio.to_thread(self.store.create_run, run)

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
        t_fix = CoordinationTask(
            run_id=run.run_id,
            title="Stage 4: Structured Fix Loop",
            status="queued",
            assignee_session_id=implementer_session_id,
            assignee_role="fixer",
            dependencies=[t_rev.task_id],
        )
        t_test = CoordinationTask(
            run_id=run.run_id,
            title="Stage 5: Final Test & Acceptance",
            status="queued",
            assignee_session_id=reviewer_session_id,
            assignee_role="tester",
            dependencies=[t_fix.task_id],
        )

        for task in (t_plan, t_impl, t_rev, t_fix, t_test):
            await asyncio.to_thread(self.store.create_task, task)

        # Planner receives the durable kickoff message immediately.
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

        evt = CoordinationEvent(
            root_session_id=root_session_id,
            run_id=run.run_id,
            task_id=t_plan.task_id,
            actor_session_id=root_session_id,
            event_type="workflow.started",
            payload={"run_id": run.run_id, "title": title, "prompt": user_prompt},
        )
        await asyncio.to_thread(self.store.record_event, evt)

        _logger.info(
            "Started %s run %s for session %s",
            run.template,
            run.run_id,
            root_session_id,
        )
        return run

    async def advance(
        self,
        *,
        run_id: str,
        task_id: str,
        outcome: WorkflowOutcome,
        artifacts: list[dict[str, object]] | None = None,
        review_decision: str | None = None,
    ) -> CoordinationRun:
        """Advance the fixed workflow after one stage's agent reports.

        Only the owning task transitions, and only into the machine's next
        state: a duplicate delivery of the same stage result cannot replay a
        completed stage. ``review_decision`` is ``approved`` by default for a
        successful reviewer; ``changes_requested`` routes to the fix stage.
        """
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run is None:
            raise ValueError(f"run {run_id!r} not found")
        task = await asyncio.to_thread(self.store.get_task, task_id)
        if task is None or task.run_id != run_id:
            raise ValueError(f"task {task_id!r} does not belong to run {run_id!r}")

        await self._advance_task(
            run=run,
            task=task,
            outcome=outcome,
            artifacts=artifacts,
            review_decision=review_decision,
        )
        updated = await asyncio.to_thread(self.store.get_run, run_id)
        if updated is None:
            raise RuntimeError(f"run {run_id!r} disappeared during advance")
        return updated

    async def _advance_task(
        self,
        *,
        run: CoordinationRun,
        task: CoordinationTask,
        outcome: WorkflowOutcome,
        artifacts: list[dict[str, object]] | None,
        review_decision: str | None,
    ) -> None:
        role = task.assignee_role or ""
        if artifacts:
            await self._persist_artifacts(run, task, artifacts)
        if outcome != "succeeded":
            await asyncio.to_thread(
                self.store.update_task_status, task.task_id, "failed", artifacts=artifacts or []
            )
            await asyncio.to_thread(
                self.store.update_run_status, run.run_id, "needs_attention"
            )
            await self._record(run, task, "task.failed", {"task_id": task.task_id, "role": role})
            return

        # Idempotency: only terminal/running states may advance. A re-sent
        # result for an already-succeeded stage is a no-op.
        if task.status in ("succeeded", "cancelled"):
            return

        if role == "planner":
            await asyncio.to_thread(
                self.store.update_task_status, task.task_id, "succeeded", artifacts=artifacts or []
            )
            impl = await self._stage_task(run.run_id, "implementer")
            if impl:
                await asyncio.to_thread(self.store.update_task_status, impl.task_id, "running")
                await self._send(
                    run,
                    impl,
                    "task.request",
                    {
                        "stage": "implementation",
                        "prompt": "Implement the plan above; run the tests you add or update.",
                    },
                )
            await self._record(run, task, "workflow.stage.advanced", {"stage": "implement"})
            return

        if role == "implementer":
            await asyncio.to_thread(
                self.store.update_task_status, task.task_id, "succeeded", artifacts=artifacts or []
            )
            reviewer = await self._stage_task(run.run_id, "reviewer")
            if reviewer:
                await asyncio.to_thread(self.store.update_task_status, reviewer.task_id, "running")
                await self._send(
                    run,
                    reviewer,
                    "review.request",
                    {
                        "stage": "review",
                        "prompt": (
                            "Review the implementation diff and report approved or "
                            "changes_requested."
                        ),
                        "impl_task_id": task.task_id,
                    },
                )
            await self._record(run, task, "workflow.stage.advanced", {"stage": "review"})
            return

        if role == "reviewer":
            decision = (review_decision or "approved").strip().lower()
            if decision == "changes_requested":
                await asyncio.to_thread(
                    self.store.update_task_status,
                    task.task_id,
                    "waiting_review",
                    artifacts=artifacts or [],
                )
                fixer = await self._stage_task(run.run_id, "fixer")
                if fixer:
                    await asyncio.to_thread(
                        self.store.update_task_status, fixer.task_id, "running"
                    )
                    await self._send(
                        run,
                        fixer,
                        "review.feedback",
                        {
                            "stage": "fix",
                            "prompt": "Address the reviewer feedback and re-run relevant tests.",
                            "review_task_id": task.task_id,
                        },
                    )
                await self._record(
                    run, task, "workflow.changes_requested", {"review_task_id": task.task_id}
                )
                return
            # Approved: fix stage is a no-op, test runs next.
            await asyncio.to_thread(
                self.store.update_task_status, task.task_id, "succeeded", artifacts=artifacts or []
            )
            fixer = await self._stage_task(run.run_id, "fixer")
            if fixer:
                await asyncio.to_thread(self.store.update_task_status, fixer.task_id, "succeeded")
            tester = await self._stage_task(run.run_id, "tester")
            if tester:
                await asyncio.to_thread(self.store.update_task_status, tester.task_id, "running")
                await self._send(
                    run,
                    tester,
                    "test.request",
                    {
                        "stage": "test",
                        "prompt": "Run the full acceptance suite and report succeeded or failed.",
                    },
                )
            await self._record(
                run, task, "workflow.review.approved", {"review_task_id": task.task_id}
            )
            return

        if role == "fixer":
            await asyncio.to_thread(
                self.store.update_task_status, task.task_id, "succeeded", artifacts=artifacts or []
            )
            reviewer = await self._stage_task(run.run_id, "reviewer")
            if reviewer:
                await asyncio.to_thread(self.store.update_task_status, reviewer.task_id, "running")
                await self._send(
                    run,
                    reviewer,
                    "review.request",
                    {
                        "stage": "re_review",
                        "prompt": (
                            "Re-review the fix artifacts and report approved or "
                            "changes_requested."
                        ),
                        "fix_task_id": task.task_id,
                    },
                )
            await self._record(run, task, "workflow.fix.succeeded", {"fix_task_id": task.task_id})
            return

        if role == "tester":
            await asyncio.to_thread(
                self.store.update_task_status, task.task_id, "succeeded", artifacts=artifacts or []
            )
            await asyncio.to_thread(self.store.update_run_status, run.run_id, "succeeded")
            metadata = dict(run.metadata)
            metadata["stage"] = "accepted"
            metadata["fix_cycles"] = int(metadata.get("fix_cycles") or 0)
            await self._update_run_metadata(run.run_id, metadata)
            await self._record(run, task, "workflow.succeeded", {"test_task_id": task.task_id})
            return

    async def _update_run_metadata(self, run_id: str, metadata: dict[str, object]) -> None:
        await asyncio.to_thread(self.store.update_run_metadata, run_id, metadata)

    async def _stage_task(self, run_id: str, role: str) -> CoordinationTask | None:
        for task in await asyncio.to_thread(self.store.list_tasks, run_id):
            if task.assignee_role == role:
                return task
        return None

    async def _persist_artifacts(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
        artifacts: list[dict[str, object]],
    ) -> None:
        """Persist stage artifacts as durable metadata rows, one per named item."""
        existing = await asyncio.to_thread(
            self.store.list_artifacts,
            run.root_session_id,
            run_id=run.run_id,
            task_id=task.task_id,
        )
        existing_names = {str(a.metadata.get("name", "")) for a in existing}
        for item in artifacts:
            name = str(item.get("name") or item.get("kind") or "artifact")
            if name in existing_names:
                continue
            raw_kind = str(item.get("kind") or name).lower()
            if any(marker in raw_kind for marker in ("diff", "patch")):
                kind = "diff"
            elif "plan" in raw_kind:
                kind = "plan"
            elif "report" in raw_kind:
                kind = "report"
            elif "test" in raw_kind:
                kind = "test_result"
            elif "log" in raw_kind:
                kind = "log"
            else:
                kind = "other"
            artifact = CoordinationArtifact(
                root_session_id=run.root_session_id,
                run_id=run.run_id,
                task_id=task.task_id,
                producer_session_id=task.assignee_session_id or run.root_session_id,
                kind=kind,  # type: ignore[arg-type]
                digest=str(item["digest"]) if item.get("digest") else None,
                uri=str(item["uri"]) if item.get("uri") else None,
                metadata={
                    "name": name,
                    **{
                        str(key): value
                        for key, value in item.items()
                        if key not in ("name", "kind", "digest", "uri")
                    },
                },
            )
            await asyncio.to_thread(self.store.create_artifact, artifact)

    async def _send(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
        intent: str,
        payload: dict[str, object],
    ) -> None:
        msg = AgentMessage(
            root_session_id=run.root_session_id,
            run_id=run.run_id,
            task_id=task.task_id,
            sender_session_id=run.root_session_id,
            sender_role="user_orchestrator",
            recipient_session_id=task.assignee_session_id or run.root_session_id,
            recipient_role=task.assignee_role,
            intent=intent,
            payload=payload,
        )
        await asyncio.to_thread(self.store.save_message_and_outbox, msg)

    async def _record(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        evt = CoordinationEvent(
            root_session_id=run.root_session_id,
            run_id=run.run_id,
            task_id=task.task_id,
            actor_session_id=task.assignee_session_id,
            event_type=event_type,
            payload=payload,
        )
        await asyncio.to_thread(self.store.record_event, evt)
