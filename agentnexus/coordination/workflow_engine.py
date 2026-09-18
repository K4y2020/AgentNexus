"""Resumable fixed Plan -> Implement -> Review -> Fix -> Test workflow engine.

Runs the P3 fixed workflow state machine on the durable coordination store.
Each stage owns a task row; ``advance`` is the only way stages move, so a
restart or API retry can resume from the exact task without replaying
already-succeeded stages. No generic DAG editor here — one fixed template by
design (P4 will generalize beyond it).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from agentnexus.coordination.behavior import BehaviorMode, workflow_behavior_payload
from agentnexus.coordination.store import (
    CoordinationStore,
    StateTransitionConflict,
    get_default_coordination_db_path,
)
from agentnexus.coordination.types import (
    AgentMessage,
    CoordinationArtifact,
    CoordinationEvent,
    CoordinationRun,
    CoordinationTask,
    OutboxItem,
    generate_coordination_id,
)
from agentnexus.workspaces.lease import WorkspaceCoordinator

_logger = logging.getLogger(__name__)

WorkflowOutcome = Literal["succeeded", "failed"]

#: Run metadata key holding the last healing timestamp per task, so a task
#: whose dispatch can never land is not re-queued on every scheduler pass.
DISPATCH_HEAL_COOLDOWN_KEY = "dispatch_heal_cooldown"
DISPATCH_HEAL_COOLDOWN_S = 60.0
DISPATCH_HEAL_ATTEMPTS_KEY = "dispatch_heal_attempts"
MAX_DISPATCH_HEALS_PER_TASK = 1


def _workflow_idempotency_key(*parts: object) -> str:
    """Return a compact stable key for one logical workflow dispatch."""
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"workflow:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _delivery_in_flight(message: AgentMessage, outbox_items: list[OutboxItem]) -> bool:
    """Whether a message is still owed a delivery, or waiting on a live one.

    Recovery must not touch two cases: a row still ``pending``/``leased`` is
    genuinely being retried, and a row already ``confirmed``/``injected`` is
    waiting for the agent to consume it, so re-sending would duplicate the
    work. Everything else — rows that are ``failed``, ``unknown``, or simply
    gone — means nothing will ever deliver this message.

    A message the recipient already answered (``rejected``) is left alone too:
    rejection is only recorded when a run is cancelled or a task is reassigned,
    and re-sending superseded work would resurrect it.
    """
    if message.consumption_state != "unconsumed":
        return True
    if not outbox_items:
        return False
    live_statuses = ("pending", "leased", "confirmed", "injected")
    return any(item.status in live_statuses for item in outbox_items)


_RESULT_LINE = (
    "End your reply with exactly [WORKFLOW_RESULT: succeeded] or [WORKFLOW_RESULT: failed]."
)
_REVIEW_LINE = (
    "End your reply with exactly [REVIEW_DECISION: approved] "
    "or [REVIEW_DECISION: changes_requested]."
)

# Fixed template acceptance evidence for each stage. These are the durable
# per-task criteria the workflow engine binds at creation, so a stage result
# is only trustworthy when the completing agent reports evidence for them.
_TASK_ACCEPTANCE: dict[str, list[str]] = {
    "planner": ["Plan includes task breakdown and explicit acceptance criteria"],
    "implementer": ["Implementation satisfies the plan", "Added/updated tests pass"],
    "reviewer": ["Review verdict includes evidence and blocking issues if any"],
    "fixer": ["Every reviewer feedback item is resolved or explicitly waived"],
    "tester": ["Full acceptance suite passes", "No regressions in the diff"],
}

# Runs created from a caller-supplied template carry this marker. Generic DAG
# runs advance purely from task dependencies instead of the fixed five-stage
# role machine; the dispatcher, receipts, retries and report API stay shared.
DAG_TEMPLATE_PREFIX = "dag:"


@dataclass
class WorkflowDagTaskSpec:
    """One caller-defined stage in a template DAG."""

    name: str
    title: str
    assignee_session_id: str
    assignee_role: str
    prompt: str
    intent: str = "task.request"
    acceptance_criteria: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    behavior_mode: BehaviorMode | None = None


class CoordinationWorkflowEngine:
    """Executes and coordinates the fixed five-stage coding workflow."""

    def __init__(
        self,
        store: CoordinationStore | None = None,
        workspace_coord: WorkspaceCoordinator | None = None,
        conversation_store: Any | None = None,
    ) -> None:
        self.store = store or CoordinationStore(get_default_coordination_db_path())
        self.workspace_coord = workspace_coord or WorkspaceCoordinator()
        self.conversation_store = conversation_store

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
        budget: dict[str, object] | None = None,
        behavior_modes: dict[str, BehaviorMode] | None = None,
    ) -> CoordinationRun:
        """Initialize and kick off the fixed Plan -> Implement -> Review -> Fix -> Test run."""
        run = CoordinationRun(
            title=title,
            root_session_id=root_session_id,
            template="plan_implement_review_fix_test",
            status="running",
            budget=dict(budget or {}),
            metadata={
                "planner_id": planner_session_id,
                "implementer_id": implementer_session_id,
                "reviewer_id": reviewer_session_id,
                "workspace_path": workspace_path,
                "initial_prompt": user_prompt,
                "stage": "planning",
                "fix_cycles": 0,
                "template_version": "1.0",
                "behavior_modes": dict(behavior_modes or {}),
            },
        )
        await asyncio.to_thread(self.store.create_run, run)

        task_deadline_s = dict(budget or {}).get("task_deadline_s")
        task_deadline: float | None = None
        if task_deadline_s:
            try:
                task_deadline = time.time() + float(task_deadline_s)
            except (TypeError, ValueError):
                task_deadline = None

        t_plan = CoordinationTask(
            run_id=run.run_id,
            title="Stage 1: Architecture & Task Planning",
            status="assigned",
            assignee_session_id=planner_session_id,
            assignee_role="planner",
            acceptance_criteria=_TASK_ACCEPTANCE["planner"],
            deadline=task_deadline,
        )
        t_impl = CoordinationTask(
            run_id=run.run_id,
            title="Stage 2: Implementation & Testing",
            status="queued",
            assignee_session_id=implementer_session_id,
            assignee_role="implementer",
            dependencies=[t_plan.task_id],
            acceptance_criteria=_TASK_ACCEPTANCE["implementer"],
            deadline=task_deadline,
        )
        t_rev = CoordinationTask(
            run_id=run.run_id,
            title="Stage 3: Code Review & Verification",
            status="queued",
            assignee_session_id=reviewer_session_id,
            assignee_role="reviewer",
            dependencies=[t_impl.task_id],
            acceptance_criteria=_TASK_ACCEPTANCE["reviewer"],
            deadline=task_deadline,
        )
        t_fix = CoordinationTask(
            run_id=run.run_id,
            title="Stage 4: Structured Fix Loop",
            status="queued",
            assignee_session_id=implementer_session_id,
            assignee_role="fixer",
            dependencies=[t_rev.task_id],
            acceptance_criteria=_TASK_ACCEPTANCE["fixer"],
            deadline=task_deadline,
        )
        t_test = CoordinationTask(
            run_id=run.run_id,
            title="Stage 5: Final Test & Acceptance",
            status="queued",
            assignee_session_id=reviewer_session_id,
            assignee_role="tester",
            dependencies=[t_fix.task_id],
            acceptance_criteria=_TASK_ACCEPTANCE["tester"],
            deadline=task_deadline,
        )

        for task in (t_plan, t_impl, t_rev, t_fix, t_test):
            await asyncio.to_thread(self.store.create_task, task)

        # Planner receives the durable kickoff message immediately.
        kickoff_payload: dict[str, object] = {
            "stage": "planning",
            "prompt": (
                f"Please analyze and create an implementation plan for: {user_prompt}"
                f"\n\n{_RESULT_LINE}"
            ),
        }
        self._inject_behavior(run, t_plan, kickoff_payload)
        kickoff_msg = AgentMessage(
            root_session_id=root_session_id,
            run_id=run.run_id,
            task_id=t_plan.task_id,
            sender_session_id=root_session_id,
            sender_role="user_orchestrator",
            recipient_session_id=planner_session_id,
            recipient_role="planner",
            intent="task.request",
            payload=kickoff_payload,
            idempotency_key=_workflow_idempotency_key(
                run.run_id,
                t_plan.task_id,
                "initial",
                kickoff_payload,
            ),
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

    async def start_dag_workflow_run(
        self,
        *,
        title: str,
        root_session_id: str,
        tasks: list[WorkflowDagTaskSpec],
        budget: dict[str, object] | None = None,
    ) -> CoordinationRun:
        """Start a caller-defined task DAG (P4 template workflow).

        Each spec names a stage key; ``dependencies`` reference those keys.
        Root stages (no dependencies) are dispatched immediately and every
        later stage is dispatched by :meth:`_dispatch_ready_tasks` once all
        of its dependencies have succeeded. Result reporting, receipts,
        pause/resume/cancel, retry and reassign use the same durable path as
        the fixed workflow.
        """
        if not tasks:
            raise ValueError("dag workflow requires at least one task")
        seen: dict[str, str] = {}
        for task in tasks:
            if not task.name:
                raise ValueError("every dag task needs a non-empty name")
            if task.name in seen:
                raise ValueError(f"duplicate dag task name {task.name!r}")
            seen[task.name] = task.name

        template_name = f"{DAG_TEMPLATE_PREFIX}{'-'.join(t.name for t in tasks)}"
        name_to_id: dict[str, str] = {}
        for task in tasks:
            name_to_id[task.name] = generate_coordination_id("ctask")
        run = CoordinationRun(
            title=title,
            root_session_id=root_session_id,
            template=template_name,
            status="running",
            budget=dict(budget or {}),
            metadata={
                "template": "generic_dag",
                "template_version": "1.0",
                "dag_task_specs": {
                    name_to_id[task.name]: {
                        "title": task.title,
                        "name": task.name,
                        "assignee_role": task.assignee_role,
                        "prompt": task.prompt,
                        "intent": task.intent,
                        "behavior_mode": task.behavior_mode,
                    }
                    for task in tasks
                },
            },
        )
        await asyncio.to_thread(self.store.create_run, run)

        task_deadline_s = dict(budget or {}).get("task_deadline_s")
        task_deadline: float | None = None
        if task_deadline_s:
            try:
                task_deadline = time.time() + float(task_deadline_s)
            except (TypeError, ValueError):
                task_deadline = None

        created: list[CoordinationTask] = []
        for task in tasks:
            for dep in task.dependencies:
                if dep not in name_to_id:
                    raise ValueError(f"dag task {task.name!r} depends on unknown {dep!r}")
            coordination_task = CoordinationTask(
                run_id=run.run_id,
                title=task.title,
                status="assigned" if not task.dependencies else "queued",
                assignee_session_id=task.assignee_session_id,
                assignee_role=task.assignee_role,
                dependencies=[name_to_id[dep] for dep in task.dependencies],
                acceptance_criteria=task.acceptance_criteria,
                deadline=task_deadline,
            )
            coordination_task.task_id = name_to_id[task.name]
            await asyncio.to_thread(self.store.create_task, coordination_task)
            created.append(coordination_task)

        await self._dispatch_ready_tasks(run)
        await asyncio.to_thread(
            self.store.record_event,
            CoordinationEvent(
                root_session_id=run.root_session_id,
                run_id=run.run_id,
                actor_session_id=root_session_id,
                event_type="workflow.started",
                payload={
                    "run_id": run.run_id,
                    "title": title,
                    "template": template_name,
                    "task_count": len(created),
                },
            ),
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
        Concurrency is resolved with store CAS: only the caller that wins
        the state claim dispatches the next stage; a duplicate or lost call
        returns the current run unchanged.
        """
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run is None:
            raise ValueError(f"run {run_id!r} not found")
        task = await asyncio.to_thread(self.store.get_task, task_id)
        if task is None or task.run_id != run_id:
            raise ValueError(f"task {task_id!r} does not belong to run {run_id!r}")

        try:
            await self._advance_task(
                run=run,
                task=task,
                outcome=outcome,
                artifacts=artifacts,
                review_decision=review_decision,
            )
        except StateTransitionConflict:
            # The row moved between read and CAS (another caller advanced it,
            # an explicit cancel won, or a retry reset it). Duplicate results
            # are idempotent: report the authoritative current state without
            # re-dispatching the next stage.
            _logger.info(
                "workflow advance lost CAS race for run %s task %s; treating as idempotent",
                run_id,
                task_id,
            )
        updated = await asyncio.to_thread(self.store.get_run, run_id)
        if updated is None:
            raise RuntimeError(f"run {run_id!r} disappeared during advance")
        return updated

    async def _move_task(
        self,
        task_id: str,
        status: str,
        *,
        from_statuses: list[str],
        artifacts: list[dict[str, object]] | None = None,
    ) -> bool:
        """Claim a task transition with a store-side CAS.

        Returns ``True`` when this caller won the move or ``False`` for a
        harmless idempotent repeat (already in *status*). Raises
        :class:`ValueError` when the state has moved on — the workflow
        engine turns that into a durable concurrent-owner signal instead of
        blindly re-dispatching the next stage.
        """
        return await asyncio.to_thread(
            self.store.transition_task_status,
            task_id,
            status,
            from_statuses=from_statuses,
            artifacts=artifacts,
        )

    async def _advance_task(
        self,
        *,
        run: CoordinationRun,
        task: CoordinationTask,
        outcome: WorkflowOutcome,
        artifacts: list[dict[str, object]] | None,
        review_decision: str | None,
    ) -> None:
        await self._enforce_deadline(run)
        await self._enforce_task_deadline(run, task)
        role = task.assignee_role or ""
        if artifacts:
            await self._persist_artifacts(run, task, artifacts)
        if run.template.startswith(DAG_TEMPLATE_PREFIX):
            await self._advance_generic_task(
                run=run,
                task=task,
                outcome=outcome,
                artifacts=artifacts,
            )
            return
        if outcome != "succeeded":
            await self._move_task(
                task.task_id,
                "failed",
                from_statuses=["running", "assigned", "waiting_review"],
                artifacts=artifacts or [],
            )
            await asyncio.to_thread(
                self.store.transition_run_status,
                run.run_id,
                "needs_attention",
                from_statuses=["running", "waiting_peer", "reconciling"],
            )
            await self._record(run, task, "task.failed", {"task_id": task.task_id, "role": role})
            return

        # Idempotency: only terminal/running states may advance. A re-sent
        # result for an already-succeeded stage is a no-op.
        if task.status in ("succeeded", "cancelled"):
            return

        if role == "planner":
            await self._move_task(
                task.task_id,
                "succeeded",
                from_statuses=["running", "assigned"],
                artifacts=artifacts or [],
            )
            impl = await self._stage_task(run.run_id, "implementer")
            if impl:
                await self._move_task(impl.task_id, "running", from_statuses=["queued", "running"])
                prior_artifacts = await self._prior_artifacts_payload(run, task.task_id)
                await self._send(
                    run,
                    impl,
                    "task.request",
                    {
                        "stage": "implementation",
                        "prior_artifacts": prior_artifacts,
                        "prompt": (
                            "Implement per prior_artifacts; run the tests you add or update. "
                            + _RESULT_LINE
                        ),
                    },
                )
            await self._record(run, task, "workflow.stage.advanced", {"stage": "implement"})
            return

        if role == "implementer":
            await self._move_task(
                task.task_id,
                "succeeded",
                from_statuses=["running", "assigned"],
                artifacts=artifacts or [],
            )
            reviewer = await self._stage_task(run.run_id, "reviewer")
            if reviewer:
                await self._move_task(
                    reviewer.task_id, "running", from_statuses=["queued", "running"]
                )
                prior_artifacts = await self._prior_artifacts_payload(run, task.task_id)
                await self._send(
                    run,
                    reviewer,
                    "review.request",
                    {
                        "stage": "review",
                        "prior_artifacts": prior_artifacts,
                        "prompt": (
                            "Review the diff in prior_artifacts; report approved or "
                            "changes_requested. " + _REVIEW_LINE
                        ),
                        "impl_task_id": task.task_id,
                    },
                )
            await self._record(run, task, "workflow.stage.advanced", {"stage": "review"})
            return

        if role == "reviewer":
            decision = (review_decision or "approved").strip().lower()
            if decision == "changes_requested":
                await self._move_task(
                    task.task_id,
                    "waiting_review",
                    from_statuses=["running", "assigned"],
                    artifacts=artifacts or [],
                )
                fixer = await self._stage_task(run.run_id, "fixer")
                if fixer:
                    await self._move_task(
                        fixer.task_id, "running", from_statuses=["queued", "running"]
                    )
                    prior_artifacts = await self._prior_artifacts_payload(run, task.task_id)
                    await self._send(
                        run,
                        fixer,
                        "review.feedback",
                        {
                            "stage": "fix",
                            "prior_artifacts": prior_artifacts,
                            "prompt": (
                                "Address reviewer feedback in prior_artifacts; re-run tests. "
                                + _RESULT_LINE
                            ),
                            "review_task_id": task.task_id,
                        },
                    )
                await self._record(
                    run, task, "workflow.changes_requested", {"review_task_id": task.task_id}
                )
                return
            # Approved: fix stage is a no-op, test runs next.
            await self._move_task(
                task.task_id,
                "succeeded",
                from_statuses=["running", "assigned"],
                artifacts=artifacts or [],
            )
            fixer = await self._stage_task(run.run_id, "fixer")
            if fixer:
                await self._move_task(
                    fixer.task_id, "succeeded", from_statuses=["queued", "running"]
                )
            tester = await self._stage_task(run.run_id, "tester")
            if tester:
                await self._move_task(
                    tester.task_id, "running", from_statuses=["queued", "running"]
                )
                impl = await self._stage_task(run.run_id, "implementer")
                prior_artifacts = (
                    await self._prior_artifacts_payload(run, impl.task_id)
                    if impl is not None
                    else []
                )
                await self._send(
                    run,
                    tester,
                    "test.request",
                    {
                        "stage": "test",
                        "prior_artifacts": prior_artifacts,
                        "prompt": (
                            "Run acceptance suite per prior_artifacts; report succeeded/failed. "
                            + _RESULT_LINE
                        ),
                    },
                )
            await self._record(
                run, task, "workflow.review.approved", {"review_task_id": task.task_id}
            )
            return

        if role == "fixer":
            await self._move_task(
                task.task_id,
                "succeeded",
                from_statuses=["running", "assigned"],
                artifacts=artifacts or [],
            )
            reviewer = await self._stage_task(run.run_id, "reviewer")
            if reviewer:
                await self._move_task(
                    reviewer.task_id, "running", from_statuses=["waiting_review", "running"]
                )
                prior_artifacts = await self._prior_artifacts_payload(run, task.task_id)
                await self._send(
                    run,
                    reviewer,
                    "review.request",
                    {
                        "stage": "re_review",
                        "prior_artifacts": prior_artifacts,
                        "prompt": (
                            "Re-review fix artifacts in prior_artifacts; report approved or "
                            "changes_requested. " + _REVIEW_LINE
                        ),
                        "fix_task_id": task.task_id,
                    },
                )
            await self._record(run, task, "workflow.fix.succeeded", {"fix_task_id": task.task_id})
            return

        if role == "tester":
            await self._move_task(
                task.task_id,
                "succeeded",
                from_statuses=["running", "assigned"],
                artifacts=artifacts or [],
            )
            await asyncio.to_thread(
                self.store.transition_run_status,
                run.run_id,
                "succeeded",
                from_statuses=["running", "waiting_peer", "reconciling"],
            )
            metadata = dict(run.metadata)
            metadata["stage"] = "accepted"
            metadata["fix_cycles"] = int(metadata.get("fix_cycles") or 0)
            await self._update_run_metadata(run.run_id, metadata)
            await self._record(run, task, "workflow.succeeded", {"test_task_id": task.task_id})
            return

    async def _advance_generic_task(
        self,
        *,
        run: CoordinationRun,
        task: CoordinationTask,
        outcome: WorkflowOutcome,
        artifacts: list[dict[str, object]] | None,
    ) -> None:
        """Advance one caller-defined DAG stage by dependencies.

        A failed task moves the run to ``needs_attention``; a successful task
        is terminal, and every queued stage whose dependencies are all
        terminal-successful is dispatched immediately. The run succeeds only
        when every task has succeeded, matching the P4 stage-board contract.
        """
        if outcome != "succeeded":
            await self._move_task(
                task.task_id,
                "failed",
                from_statuses=["running", "assigned", "waiting_review"],
                artifacts=artifacts or [],
            )
            await asyncio.to_thread(
                self.store.transition_run_status,
                run.run_id,
                "needs_attention",
                from_statuses=["running", "waiting_peer", "reconciling"],
            )
            await self._record(run, task, "task.failed", {"task_id": task.task_id})
            return

        if task.status in ("succeeded", "cancelled"):
            return
        await self._move_task(
            task.task_id,
            "succeeded",
            from_statuses=["running", "assigned", "waiting_review"],
            artifacts=artifacts or [],
        )
        await self._record(run, task, "workflow.stage.advanced", {"task_id": task.task_id})
        await self._dispatch_ready_tasks(run)
        if await self._all_tasks_succeeded(run.run_id):
            await asyncio.to_thread(
                self.store.transition_run_status,
                run.run_id,
                "succeeded",
                from_statuses=["running", "waiting_peer", "reconciling"],
            )
            await self._record(run, task, "workflow.succeeded", {"task_id": task.task_id})

    def _workflow_behavior_mode(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
    ) -> BehaviorMode | None:
        """Return the workflow-node behavior override for one task, if any."""
        mode: object = None
        if run.template.startswith(DAG_TEMPLATE_PREFIX):
            spec = dict(run.metadata.get("dag_task_specs", {}).get(task.task_id, {}) or {})
            mode = spec.get("behavior_mode")
        else:
            modes = dict(run.metadata.get("behavior_modes") or {})
            role = task.assignee_role or ""
            mode = modes.get(role)
        if mode in ("off", "advisory", "lean", "strict"):
            return mode  # type: ignore[return-value]
        return None

    def _inject_behavior(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
        payload: dict[str, object],
    ) -> None:
        """Resolve and persist the node's Behavior Pack fact on a message."""
        behavior = workflow_behavior_payload(
            role=task.assignee_role,
            workflow_mode=self._workflow_behavior_mode(run, task),
        )
        payload["behavior_binding"] = behavior
        instructions = behavior["resolved"].get("instructions") or []
        if instructions:
            prompt = payload.get("prompt") or ""
            block = "Behavior instructions:\n" + "".join(f"- {line}\n" for line in instructions)
            payload["prompt"] = f"{block}{prompt}"

    async def _dispatch_ready_tasks(self, run: CoordinationRun) -> None:
        """Dispatch every queued DAG stage whose dependencies are terminal."""
        if run.status not in ("running", "waiting_peer", "reconciling"):
            return
        tasks = await asyncio.to_thread(self.store.list_tasks, run.run_id)
        successful: set[str] = set()
        for task in tasks:
            if task.status == "succeeded":
                successful.add(task.task_id)
        ready = [
            task
            for task in tasks
            if task.status == "queued"
            and task.assignee_session_id
            and all(dep in successful for dep in task.dependencies)
        ]
        for task in ready:
            if not await self._move_task(
                task.task_id,
                "running",
                from_statuses=["queued", "running"],
            ):
                continue
            spec = dict(run.metadata.get("dag_task_specs", {}).get(task.task_id, {}) or {})
            prompt = str(spec.get("prompt") or task.title or "Proceed with the assigned task.")
            intent = str(spec.get("intent") or "task.request")
            payload: dict[str, object] = {
                "stage": task.assignee_role or "stage",
                "prompt": prompt,
            }
            if task.acceptance_criteria:
                payload["acceptance_criteria"] = list(task.acceptance_criteria)
            if task.assignee_role == "reviewer":
                payload["prompt"] = f"{prompt}\n\n{_REVIEW_LINE}"
            else:
                payload["prompt"] = f"{prompt}\n\n{_RESULT_LINE}"
            await self._send(run, task, intent, payload)
            await self._record(
                run,
                task,
                "workflow.stage.dispatched",
                {
                    "task_id": task.task_id,
                    "dependencies": task.dependencies,
                    "message_intent": intent,
                },
            )

    async def _all_tasks_succeeded(self, run_id: str) -> bool:
        tasks = await asyncio.to_thread(self.store.list_tasks, run_id)
        return bool(tasks) and all(task.status == "succeeded" for task in tasks)

    def _deadline_exceeded(self, run: CoordinationRun) -> bool:
        deadline = run.budget.get("deadline_s")
        if not deadline:
            return False
        try:
            return time.time() >= float(deadline)
        except (TypeError, ValueError):
            return False

    async def _enforce_deadline(self, run: CoordinationRun) -> None:
        """Stop automatic dispatch once a run's hard deadline has passed."""
        if not self._deadline_exceeded(run):
            return
        if run.status != "needs_attention":
            with contextlib.suppress(StateTransitionConflict):
                # Another deadline/cancel owner already moved the run.
                await asyncio.to_thread(
                    self.store.transition_run_status,
                    run.run_id,
                    "needs_attention",
                    from_statuses=[
                        "draft",
                        "running",
                        "paused",
                        "waiting_user",
                        "waiting_peer",
                        "reconciling",
                    ],
                )
        await asyncio.to_thread(
            self.store.record_event,
            CoordinationEvent(
                root_session_id=run.root_session_id,
                run_id=run.run_id,
                actor_session_id=run.root_session_id,
                event_type="workflow.deadline_exceeded",
                payload={"run_id": run.run_id, "deadline_s": run.budget.get("deadline_s")},
            ),
        )
        raise ValueError(f"workflow deadline exceeded for run {run.run_id}")

    def _task_deadline_exceeded(self, task: CoordinationTask) -> bool:
        if task.deadline is None:
            return False
        try:
            return time.time() >= float(task.deadline)
        except (TypeError, ValueError):
            return False

    async def _enforce_task_deadline(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
    ) -> None:
        """Stop dispatch on an expired per-task deadline (fail closed)."""
        if not self._task_deadline_exceeded(task):
            return
        if task.status not in ("succeeded", "cancelled"):
            with contextlib.suppress(StateTransitionConflict):
                await asyncio.to_thread(
                    self.store.transition_task_status,
                    task.task_id,
                    "blocked",
                    from_statuses=[
                        "draft",
                        "queued",
                        "assigned",
                        "running",
                        "waiting_user",
                        "waiting_peer",
                        "waiting_review",
                        "reconciling",
                    ],
                )
            with contextlib.suppress(StateTransitionConflict):
                await asyncio.to_thread(
                    self.store.transition_run_status,
                    run.run_id,
                    "needs_attention",
                    from_statuses=[
                        "draft",
                        "running",
                        "paused",
                        "waiting_user",
                        "waiting_peer",
                        "reconciling",
                    ],
                )
        await asyncio.to_thread(
            self.store.record_event,
            CoordinationEvent(
                root_session_id=run.root_session_id,
                run_id=run.run_id,
                task_id=task.task_id,
                actor_session_id=task.assignee_session_id,
                event_type="workflow.task.deadline_exceeded",
                payload={"task_id": task.task_id, "deadline": task.deadline},
            ),
        )
        raise ValueError(f"task {task.task_id} deadline exceeded")

    async def harvest_expired_task_deadlines_once(
        self, *, now: float | None = None
    ) -> dict[str, int]:
        """Background sweep for tasks whose deadline expired with no in-band
        transition.

        ``_enforce_task_deadline`` only runs at dispatch/advance hand-off
        points, so a task stuck in an active state past its deadline is never
        reaped and its run lingers in ``running`` forever. The scheduler calls
        this periodically to fail closed: task → ``blocked``, run →
        ``needs_attention``, plus a ``workflow.task.deadline_exceeded`` event.
        Idempotent — harvested tasks leave the active status set.
        """
        check = time.time() if now is None else now
        tasks = await asyncio.to_thread(
            self.store.list_expired_active_tasks, now=check
        )
        harvested = 0
        for task in tasks:
            run = await asyncio.to_thread(self.store.get_run, task.run_id)
            if run is None:
                continue
            applied = False
            with contextlib.suppress(StateTransitionConflict):
                applied = await asyncio.to_thread(
                    self.store.transition_task_status,
                    task.task_id,
                    "blocked",
                    from_statuses=[
                        "draft",
                        "queued",
                        "assigned",
                        "running",
                        "waiting_user",
                        "waiting_peer",
                        "waiting_review",
                        "reconciling",
                    ],
                )
            if not applied:
                continue
            harvested += 1
            with contextlib.suppress(StateTransitionConflict):
                await asyncio.to_thread(
                    self.store.transition_run_status,
                    run.run_id,
                    "needs_attention",
                    from_statuses=[
                        "draft",
                        "running",
                        "paused",
                        "waiting_user",
                        "waiting_peer",
                        "reconciling",
                    ],
                )
            await asyncio.to_thread(
                self.store.record_event,
                CoordinationEvent(
                    root_session_id=run.root_session_id,
                    run_id=run.run_id,
                    task_id=task.task_id,
                    actor_session_id=task.assignee_session_id,
                    event_type="workflow.task.deadline_exceeded",
                    payload={
                        "task_id": task.task_id,
                        "deadline": task.deadline,
                        "harvester": True,
                    },
                ),
            )
        return {"expired": len(tasks), "harvested": harvested}

    async def _update_run_metadata(self, run_id: str, metadata: dict[str, object]) -> None:
        await asyncio.to_thread(self.store.update_run_metadata, run_id, metadata)

    async def pause_run(self, run_id: str) -> CoordinationRun:
        """Pause a run whose stages are still advancing (idempotent)."""
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run is None:
            raise ValueError(f"run {run_id!r} not found")
        await asyncio.to_thread(
            self.store.transition_run_status,
            run_id,
            "paused",
            from_statuses=["running", "waiting_user", "waiting_peer", "reconciling"],
        )
        await self._record_run_event(run, "workflow.run.paused", {"run_id": run_id})
        return await self._current_run(run_id)

    async def resume_run(self, run_id: str) -> CoordinationRun:
        """Resume a paused run (idempotent for an already-running run)."""
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run is None:
            raise ValueError(f"run {run_id!r} not found")
        await asyncio.to_thread(
            self.store.transition_run_status,
            run_id,
            "running",
            from_statuses=["paused"],
        )
        await self._record_run_event(run, "workflow.run.resumed", {"run_id": run_id})
        return await self._current_run(run_id)

    async def cancel_run(self, run_id: str) -> CoordinationRun:
        """Cancel a run and every non-terminal task it owns (idempotent).

        Queued messages are cancelled; already-injected unconsumed task
        messages are marked ``rejected`` with a cancellation receipt so the
        UI shows the workflow was cancelled rather than falsely succeeded or
        silently left open. Already-acknowledged deliveries remain as their
        observed history.
        """
        run = await asyncio.to_thread(self.store.get_run, run_id)
        if run is None:
            raise ValueError(f"run {run_id!r} not found")
        if run.status != "cancelled":
            await asyncio.to_thread(
                self.store.transition_run_status,
                run_id,
                "cancelled",
                from_statuses=[
                    "draft",
                    "running",
                    "paused",
                    "waiting_user",
                    "waiting_peer",
                    "reconciling",
                    "needs_attention",
                ],
            )
        tasks = await asyncio.to_thread(self.store.list_tasks, run_id)
        non_terminal = {
            "draft",
            "queued",
            "assigned",
            "running",
            "blocked",
            "waiting_user",
            "waiting_peer",
            "waiting_review",
            "reconciling",
            "needs_attention",
        }
        for task in tasks:
            if task.status in non_terminal:
                await self._move_task(task.task_id, "cancelled", from_statuses=list(non_terminal))
        for task in tasks:
            await self._cancel_run_messages(run, task)
        await self._record_run_event(
            run,
            "workflow.run.cancelled",
            {"run_id": run_id, "task_count": len(tasks)},
        )
        return await self._current_run(run_id)

    async def _record_run_event(
        self,
        run: CoordinationRun,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        """Append an orchestration-level event (no task-owned actor)."""
        await asyncio.to_thread(
            self.store.record_event,
            CoordinationEvent(
                root_session_id=run.root_session_id,
                run_id=run.run_id,
                actor_session_id=run.root_session_id,
                event_type=event_type,
                payload=payload,
            ),
        )

    async def _cancel_run_messages(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
    ) -> None:
        """Cancel queued deliveries and reject active unconsumed task messages."""
        messages = await asyncio.to_thread(
            self.store.list_messages,
            run.root_session_id,
        )
        for message in messages:
            if message.run_id != run.run_id or message.task_id != task.task_id:
                continue
            if message.message_state == "queued":
                await asyncio.to_thread(self.store.cancel_message, message.message_id)
                continue
            if message.message_state == "active" and message.consumption_state == "unconsumed":
                await asyncio.to_thread(
                    self.store.record_consumption_receipt,
                    message.message_id,
                    "rejected",
                    {"source": "workflow_cancelled", "run_id": run.run_id},
                )
                await asyncio.to_thread(
                    self.store.record_event,
                    CoordinationEvent(
                        root_session_id=run.root_session_id,
                        run_id=run.run_id,
                        task_id=task.task_id,
                        actor_session_id=run.root_session_id,
                        event_type="message.rejected",
                        payload={
                            "message_id": message.message_id,
                            "reason": "workflow_cancelled",
                        },
                    ),
                )

    async def retry_task(self, task_id: str) -> CoordinationRun:
        """Retry one failed stage task on the same run/task identity.

        The failed task moves back to ``running``, the owning run resumes
        (``needs_attention`` -> ``running``), and the same stage request is
        re-queued through the durable outbox. A new attempt message keeps
        the same ``task_id``/``correlation`` lineage per the plan; already
        committed sibling stages are never touched.
        """
        task = await asyncio.to_thread(self.store.get_task, task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} not found")
        run = await asyncio.to_thread(self.store.get_run, task.run_id)
        if run is None:
            raise ValueError(f"run {task.run_id!r} not found")
        if task.status != "failed":
            # Idempotent contract: a duplicate retry after the claim (or a
            # terminal/cancelled task) is a no-op, never a second dispatch.
            return await self._current_run(run.run_id)
        if not task.assignee_session_id:
            raise ValueError(f"task {task_id} has no assignee to retry")
        await self._enforce_deadline(run)
        await self._enforce_task_deadline(run, task)

        current = await asyncio.to_thread(self.store.get_run, run.run_id)
        current_metadata = dict(current.metadata if current is not None else run.metadata)
        retry_count = int(current_metadata.get("retry_count") or 0)
        max_retries = int(run.budget.get("max_retries") or 0)
        if max_retries > 0 and retry_count >= max_retries:
            await self._record(
                run,
                task,
                "workflow.retry_limit_reached",
                {
                    "task_id": task_id,
                    "retry_count": retry_count,
                    "max_retries": max_retries,
                },
            )
            raise ValueError(
                f"retry limit reached ({retry_count}/{max_retries}) for task {task_id}"
            )

        claimed = await self._move_task(task_id, "running", from_statuses=["failed"])
        if not claimed:
            # A concurrent retry already claimed the slot; do not resend a
            # second stage request or double-count the attempt.
            return await self._current_run(run.run_id)
        await asyncio.to_thread(
            self.store.transition_run_status,
            run.run_id,
            "running",
            from_statuses=["needs_attention", "running"],
        )
        current_metadata["retry_count"] = retry_count + 1
        sent = await self._resend_stage_request(run, task)
        retry_count = retry_count + 1
        current_metadata["stage"] = task.assignee_role or current_metadata.get("stage")
        await self._update_run_metadata(run.run_id, current_metadata)
        await self._record(
            run,
            task,
            "workflow.task.retried",
            {"task_id": task_id, "retry_count": retry_count, "message_id": sent.message_id},
        )
        return await self._current_run(run.run_id)

    async def reassign_task(
        self, task_id: str, assignee_session_id: str
    ) -> tuple[CoordinationRun, bool]:
        """Redirect an active stage task to another assignee.

        Queued task messages are redirected in place so the durable outbox
        still delivers exactly one request, now to the new assignee. An
        injected-but-unconsumed message is rejected with an explicit receipt
        and a fresh stage request is queued for the replacement assignee.
        Already-acknowledged work is never silently yanked: it must be
        cancelled or retried before a reassignment.
        """
        task = await asyncio.to_thread(self.store.get_task, task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} not found")
        run = await asyncio.to_thread(self.store.get_run, task.run_id)
        if run is None:
            raise ValueError(f"run {task.run_id!r} not found")
        if task.status in ("succeeded", "cancelled"):
            raise ValueError(f"task {task_id} is {task.status}; cannot reassign")
        if not assignee_session_id:
            raise ValueError("assignee_session_id is required")
        await self._enforce_deadline(run)
        await self._enforce_task_deadline(run, task)

        messages = await asyncio.to_thread(self.store.list_messages, run.root_session_id)
        task_messages = [
            message
            for message in messages
            if message.task_id == task.task_id
            and message.recipient_session_id == task.assignee_session_id
        ]
        acknowledged = [
            message
            for message in task_messages
            if message.consumption_state in ("consumed", "acknowledged")
        ]
        if acknowledged:
            raise ValueError(
                f"task {task_id} work is already acknowledged; cancel/retry before reassigning"
            )

        old_assignee = task.assignee_session_id
        changed = await asyncio.to_thread(self.store.reassign_task, task_id, assignee_session_id)
        if not changed:
            return await self._current_run(run.run_id), False

        # The engine continues to own the in-memory task cursor after the row
        # moved; later stage prompts must target the new assignee.
        task.assignee_session_id = assignee_session_id
        active_rejected = False
        for message in task_messages:
            if message.message_state == "queued":
                await asyncio.to_thread(
                    self.store.redirect_message_recipient,
                    message.message_id,
                    assignee_session_id,
                )
                await asyncio.to_thread(
                    self.store.record_event,
                    CoordinationEvent(
                        root_session_id=run.root_session_id,
                        run_id=run.run_id,
                        task_id=task.task_id,
                        actor_session_id=run.root_session_id,
                        event_type="message.redirected",
                        payload={
                            "message_id": message.message_id,
                            "from_session_id": old_assignee,
                            "to_session_id": assignee_session_id,
                            "source": "task_reassigned",
                        },
                    ),
                )
            elif message.message_state == "active" and message.consumption_state == "unconsumed":
                await asyncio.to_thread(
                    self.store.record_consumption_receipt,
                    message.message_id,
                    "rejected",
                    {
                        "source": "task_reassigned",
                        "new_assignee": assignee_session_id,
                    },
                )
                await asyncio.to_thread(
                    self.store.record_event,
                    CoordinationEvent(
                        root_session_id=run.root_session_id,
                        run_id=run.run_id,
                        task_id=task.task_id,
                        actor_session_id=run.root_session_id,
                        event_type="message.rejected",
                        payload={
                            "message_id": message.message_id,
                            "reason": "task_reassigned",
                            "new_assignee": assignee_session_id,
                        },
                    ),
                )
                active_rejected = True

        sent: AgentMessage | None = None
        if active_rejected:
            sent = await self._resend_stage_request(
                run,
                task,
                attempt="reassign",
                previous_assignee=old_assignee,
            )
        await self._record(
            run,
            task,
            "workflow.task.reassigned",
            {
                "task_id": task_id,
                "from_session_id": old_assignee,
                "to_session_id": assignee_session_id,
                "queued_redirected": sum(1 for m in task_messages if m.message_state == "queued"),
                "active_rejected": active_rejected,
                "message_id": sent.message_id if sent else None,
            },
        )
        return await self._current_run(run.run_id), True

    async def reconcile_missing_dispatches(self) -> int:
        """Re-queue stage messages that were lost before/after a restart.

        This is the recovery owner for the fixed workflow: a running stage
        with no queued or actively delivered request is healed with the same
        durable envelope path used by retry/reassign. Already-consumed work
        is never replayed, and paused/cancelled/succeeded runs are skipped.
        """
        runs = await asyncio.to_thread(self.store.list_runs, None)
        total = 0
        for run in runs:
            try:
                total += await self._heal_run_dispatch(run)
            except Exception:
                _logger.exception("recovery failed for run %s", run.run_id)
        return total

    async def _heal_run_dispatch(self, run: CoordinationRun) -> int:
        if run.status not in ("running", "waiting_peer"):
            return 0
        if not await self._session_exists(run.root_session_id):
            await self.cancel_run(run.run_id)
            return 0
        tasks = await asyncio.to_thread(self.store.list_tasks, run.run_id)
        messages = await asyncio.to_thread(self.store.list_messages, run.root_session_id)
        heal_cooldowns = dict(run.metadata.get(DISPATCH_HEAL_COOLDOWN_KEY) or {})
        heal_attempts = dict(run.metadata.get(DISPATCH_HEAL_ATTEMPTS_KEY) or {})
        now = time.time()
        healed = 0
        cooldown_changed = False
        for task in tasks:
            if task.status not in ("assigned", "running"):
                continue
            if not task.assignee_session_id:
                continue
            if not await self._session_exists(task.assignee_session_id):
                await self._fail_unrecoverable_dispatch(
                    run,
                    task,
                    reason="assignee_session_not_found",
                )
                return healed
            open_messages = [
                message
                for message in messages
                if message.task_id == task.task_id
                and message.recipient_session_id == task.assignee_session_id
                and message.message_state in ("queued", "active")
            ]
            if any(m.consumption_state in ("consumed", "acknowledged") for m in open_messages):
                continue
            if open_messages:
                # A queued/active message used to mean "delivery is on its way".
                # It does not: an outbox row parked in a terminal state is
                # never retried, so the stage would wait forever. Ask the
                # outbox whether anything is still owed.
                outbox_by_message = await asyncio.to_thread(
                    self.store.list_outbox_items_for_messages,
                    [m.message_id for m in open_messages],
                )
                if any(
                    _delivery_in_flight(m, outbox_by_message.get(m.message_id, []))
                    for m in open_messages
                ):
                    continue
            attempt_count = int(heal_attempts.get(task.task_id, 0))
            if attempt_count >= MAX_DISPATCH_HEALS_PER_TASK:
                await self._fail_unrecoverable_dispatch(
                    run,
                    task,
                    reason="delivery_failed_after_recovery",
                )
                return healed
            # A lost dispatch can recur, so cap how often one stage is healed;
            # without this a permanently undeliverable task would re-queue a
            # message on every pass.
            if now - float(heal_cooldowns.get(task.task_id, 0.0)) < DISPATCH_HEAL_COOLDOWN_S:
                continue
            next_attempt = attempt_count + 1
            sent = await self._resend_stage_request(
                run,
                task,
                attempt="recovery",
                attempt_number=next_attempt,
            )
            await self._record(
                run,
                task,
                "workflow.dispatch.healed",
                {
                    "task_id": task.task_id,
                    "message_id": sent.message_id,
                    "stage": task.assignee_role,
                    "recipient_session_id": task.assignee_session_id,
                },
            )
            heal_cooldowns[task.task_id] = now
            heal_attempts[task.task_id] = next_attempt
            cooldown_changed = True
            healed += 1
        if cooldown_changed:
            metadata = dict(run.metadata)
            metadata[DISPATCH_HEAL_COOLDOWN_KEY] = heal_cooldowns
            metadata[DISPATCH_HEAL_ATTEMPTS_KEY] = heal_attempts
            await asyncio.to_thread(self.store.update_run_metadata, run.run_id, metadata)
        return healed

    async def _session_exists(self, session_id: str) -> bool:
        if self.conversation_store is None:
            return True
        from agentnexus.db.db_models import InvalidUuidError, uuid_to_bytes

        try:
            uuid_to_bytes(session_id)
        except InvalidUuidError:
            return False
        conversation = await asyncio.to_thread(
            self.conversation_store.get_conversation,
            session_id,
        )
        return conversation is not None

    async def _fail_unrecoverable_dispatch(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
        *,
        reason: str,
    ) -> None:
        await self._advance_task(
            run=run,
            task=task,
            outcome="failed",
            artifacts=[],
            review_decision=None,
        )
        await self._record(
            run,
            task,
            "workflow.dispatch.abandoned",
            {"task_id": task.task_id, "reason": reason},
        )

    async def _resend_stage_request(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
        *,
        attempt: str = "retry",
        attempt_number: int = 0,
        previous_assignee: str | None = None,
    ) -> AgentMessage:
        """Re-queue the durable stage prompt for a retried/reassigned task."""
        role = task.assignee_role or ""
        if run.template.startswith(DAG_TEMPLATE_PREFIX):
            spec = dict(run.metadata.get("dag_task_specs", {}).get(task.task_id, {}) or {})
            prompt = str(spec.get("prompt") or task.title or "Proceed with the assigned task.")
            if role == "reviewer":
                prompt = f"{prompt}\n\n{_REVIEW_LINE}"
            else:
                prompt = f"{prompt}\n\n{_RESULT_LINE}"
            payload: dict[str, object] = {
                "stage": role or "stage",
                "prompt": prompt,
            }
            if attempt != "retry":
                payload["attempt"] = attempt
            if previous_assignee is not None:
                payload["previous_assignee"] = previous_assignee
            self._inject_behavior(run, task, payload)
            message = AgentMessage(
                root_session_id=run.root_session_id,
                run_id=run.run_id,
                task_id=task.task_id,
                sender_session_id=run.root_session_id,
                sender_role="user_orchestrator",
                recipient_session_id=task.assignee_session_id or run.root_session_id,
                recipient_role=role,
                intent=str(spec.get("intent") or "task.request"),
                payload=payload,
                correlation_id=f"{attempt}/{task.task_id}",
                idempotency_key=_workflow_idempotency_key(
                    run.run_id,
                    task.task_id,
                    attempt,
                    attempt_number,
                    task.assignee_session_id,
                ),
            )
            await asyncio.to_thread(self.store.save_message_and_outbox, message)
            return message
        if role == "planner":
            payload: dict[str, object] = {
                "stage": "planning_retry",
                "prompt": (
                    "Previous planning attempt was not accepted; analyze and create an "
                    "implementation plan for the original request. " + _RESULT_LINE
                ),
            }
            intent = "task.request"
        elif role == "implementer":
            payload = {
                "stage": "implementation_retry",
                "prompt": (
                    "Previous implementation attempt was not accepted; implement the plan "
                    "above and run tests you add or update. " + _RESULT_LINE
                ),
            }
            intent = "task.request"
        elif role == "reviewer":
            payload = {
                "stage": "review_retry",
                "prompt": (
                    "Previous review attempt was not accepted; review the implementation "
                    "diff and report approved or changes_requested. " + _REVIEW_LINE
                ),
            }
            intent = "review.request"
        elif role == "fixer":
            payload = {
                "stage": "fix_retry",
                "prompt": (
                    "Previous fix attempt was not accepted; address the reviewer feedback "
                    "and re-run relevant tests. " + _RESULT_LINE
                ),
                "retry": True,
            }
            intent = "review.feedback"
        elif role == "tester":
            payload = {
                "stage": "test_retry",
                "prompt": (
                    "Previous test attempt was not accepted; run the full acceptance suite "
                    "and report succeeded or failed. " + _RESULT_LINE
                ),
            }
            intent = "test.request"
        else:
            raise ValueError(f"task {task.task_id} has unsupported role {role!r}")

        if attempt != "retry":
            payload["attempt"] = attempt
        if previous_assignee is not None:
            payload["previous_assignee"] = previous_assignee

        self._inject_behavior(run, task, payload)
        message = AgentMessage(
            root_session_id=run.root_session_id,
            run_id=run.run_id,
            task_id=task.task_id,
            sender_session_id=run.root_session_id,
            sender_role="user_orchestrator",
            recipient_session_id=task.assignee_session_id or run.root_session_id,
            recipient_role=task.assignee_role,
            intent=intent,
            payload=payload,
            correlation_id=f"{attempt}/{task.task_id}",
            idempotency_key=_workflow_idempotency_key(
                run.run_id,
                task.task_id,
                attempt,
                attempt_number,
                task.assignee_session_id,
            ),
        )
        await asyncio.to_thread(self.store.save_message_and_outbox, message)
        return message

    async def _current_run(self, run_id: str) -> CoordinationRun:
        updated = await asyncio.to_thread(self.store.get_run, run_id)
        if updated is None:
            raise RuntimeError(f"run {run_id!r} disappeared")
        return updated

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

    async def _prior_artifacts_payload(
        self,
        run: CoordinationRun,
        task_id: str,
    ) -> list[dict[str, object]]:
        """Serialize a completed stage's persisted artifacts for the next stage."""
        artifacts = await asyncio.to_thread(
            self.store.list_artifacts,
            run.root_session_id,
            run_id=run.run_id,
            task_id=task_id,
        )
        return [
            {
                "kind": a.kind,
                "name": (a.metadata or {}).get("name") or "",
                "content": (a.metadata or {}).get("content"),
                "uri": a.uri,
                "digest": a.digest,
            }
            for a in artifacts
        ]

    async def _send(
        self,
        run: CoordinationRun,
        task: CoordinationTask,
        intent: str,
        payload: dict[str, object],
    ) -> None:
        self._inject_behavior(run, task, payload)
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
            idempotency_key=_workflow_idempotency_key(
                run.run_id,
                task.task_id,
                intent,
                payload,
            ),
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
