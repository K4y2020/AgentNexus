"""FastAPI REST routes for AgentNexus Multi-Agent Coordination Control Plane (/v1/coordination)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from omnigent.coordination.store import CoordinationStore, get_default_coordination_db_path
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationRun,
    CoordinationTask,
    MessageKind,
)
from omnigent.coordination.workflow_engine import CoordinationWorkflowEngine
from omnigent.workspaces.lease import WorkspaceCoordinator, WorkspaceLeaseManager

router = APIRouter(prefix="/v1/coordination", tags=["Coordination"])
_store_override: CoordinationStore | None = None
_fallback_store: CoordinationStore | None = None


def set_coordination_store(store: CoordinationStore | None) -> None:
    """Bind the control-plane store used by the server (tests inject theirs)."""
    global _store_override
    _store_override = store


def get_coordination_store() -> CoordinationStore:
    """Return the bound control-plane store, falling back to the default DB."""
    return _store_override or _get_fallback_store()


def _get_fallback_store() -> CoordinationStore:
    global _fallback_store
    if _fallback_store is None:
        _fallback_store = CoordinationStore(get_default_coordination_db_path())
    return _fallback_store


def _request_store(request: Request) -> CoordinationStore:
    return getattr(request.app.state, "coordination_store", None) or get_coordination_store()


def _request_lease_manager(request: Request) -> WorkspaceLeaseManager:
    return getattr(request.app.state, "workspace_lease_manager", None) or WorkspaceLeaseManager(
        _request_store(request)
    )


def _request_workspace_coord(request: Request) -> WorkspaceCoordinator:
    return getattr(request.app.state, "workspace_coordinator", None) or WorkspaceCoordinator(
        _request_lease_manager(request)
    )


def _request_workflow_engine(request: Request) -> CoordinationWorkflowEngine:
    bound = getattr(request.app.state, "coordination_workflow_engine", None)
    if bound is not None:
        return bound
    return CoordinationWorkflowEngine(
        _request_store(request),
        _request_workspace_coord(request),
    )


async def _require_coordination_tree(
    request: Request,
    root_session_id: str,
    *session_ids: str,
) -> None:
    """Reject roots/senders/recipients that are not real sessions in one tree."""

    store = getattr(request.app.state, "conversation_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="coordination routes require a conversation store",
        )

    root = await asyncio.to_thread(store.get_conversation, root_session_id)
    if root is None:
        raise HTTPException(
            status_code=404, detail=f"root_session_id {root_session_id!r} not found"
        )

    for session_id in session_ids:
        if session_id == root_session_id:
            continue
        conv = await asyncio.to_thread(store.get_conversation, session_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        root_id = getattr(conv, "root_conversation_id", None)
        if root_id:
            if root_id != root_session_id:
                raise HTTPException(
                    status_code=403,
                    detail=f"session {session_id!r} does not belong to root {root_session_id!r}",
                )
            continue
        cursor = conv
        while cursor is not None and cursor.id != root_session_id:
            parent_id = getattr(cursor, "parent_conversation_id", None)
            if not parent_id:
                break
            cursor = await asyncio.to_thread(store.get_conversation, parent_id)
        if cursor is None or cursor.id != root_session_id:
            raise HTTPException(
                status_code=403,
                detail=f"session {session_id!r} does not belong to root {root_session_id!r}",
            )


# ── Pydantic Request / Response Models ────────────────────────


class SendMessageRequest(BaseModel):
    root_session_id: str
    sender_session_id: str
    recipient_session_id: str
    sender_role: str = "general"
    recipient_role: str | None = None
    kind: MessageKind = "content"
    intent: str = "task.request"
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    correlation_id: str | None = None
    in_reply_to: str | None = None
    idempotency_key: str | None = None


class CreateRunRequest(BaseModel):
    title: str = "Collaborative Coding Run"
    root_session_id: str
    template: str = "plan_implement_review"
    budget: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateTaskRequest(BaseModel):
    run_id: str
    title: str
    assignee_session_id: str | None = None
    assignee_role: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class StartWorkflowRequest(BaseModel):
    title: str = "Plan -> Implement -> Review Workflow"
    root_session_id: str
    planner_session_id: str
    implementer_session_id: str
    reviewer_session_id: str
    user_prompt: str
    workspace_path: str = "."


class AdvanceWorkflowRequest(BaseModel):
    outcome: Literal["succeeded", "failed"] = "succeeded"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    review_decision: str | None = None


class AcquireLeaseRequest(BaseModel):
    root_session_id: str
    workspace_path: str
    holder_session_id: str
    mode: str = "write"
    duration_s: float = 600.0


# ── Message Endpoints ─────────────────────────────────────────


@router.post("/messages")
async def send_coordination_message(
    req: SendMessageRequest,
    request: Request,
) -> dict[str, Any]:
    """Send a durable peer message from one agent to another and queue for delivery."""
    await _require_coordination_tree(
        request,
        req.root_session_id,
        req.sender_session_id,
        req.recipient_session_id,
    )
    if req.sender_role == "user_orchestrator" and req.sender_session_id != req.root_session_id:
        raise HTTPException(
            status_code=403,
            detail="user_orchestrator messages must be sent by root_session_id",
        )
    msg = AgentMessage(
        root_session_id=req.root_session_id,
        sender_session_id=req.sender_session_id,
        sender_role=req.sender_role,
        recipient_session_id=req.recipient_session_id,
        recipient_role=req.recipient_role,
        kind=req.kind,
        intent=req.intent,
        payload=req.payload,
        artifacts=req.artifacts,
        correlation_id=req.correlation_id,
        in_reply_to=req.in_reply_to,
        idempotency_key=req.idempotency_key,
    )
    store = _request_store(request)
    saved_msg, outbox = await asyncio.to_thread(store.save_message_and_outbox, msg)
    return {
        "message": saved_msg.to_dict(),
        "outbox_item_id": outbox.item_id,
        "delivery_state": outbox.status,
    }


@router.get("/messages")
async def list_coordination_messages(
    request: Request,
    root_session_id: str = Query(..., description="The parent/root conversation id"),
    recipient_session_id: str | None = Query(None, description="Optional target session filter"),
) -> dict[str, Any]:
    """List peer messages within a coordination room/root session."""
    if recipient_session_id:
        await _require_coordination_tree(request, root_session_id, recipient_session_id)
    else:
        await _require_coordination_tree(request, root_session_id)
    store = _request_store(request)
    messages = await asyncio.to_thread(store.list_messages, root_session_id, recipient_session_id)
    return {"messages": [m.to_dict() for m in messages]}


# ── Run Endpoints ─────────────────────────────────────────────


@router.post("/runs")
async def create_coordination_run(req: CreateRunRequest, request: Request) -> dict[str, Any]:
    """Create a new multi-agent collaboration run."""
    await _require_coordination_tree(request, req.root_session_id)
    run = CoordinationRun(
        title=req.title,
        root_session_id=req.root_session_id,
        template=req.template,
        budget=req.budget,
        metadata=req.metadata,
    )
    store = _request_store(request)
    created = await asyncio.to_thread(store.create_run, run)
    return {"run": created.to_dict()}


@router.get("/runs/{run_id}")
async def get_coordination_run(run_id: str, request: Request) -> dict[str, Any]:
    """Retrieve details and tasks for a specific coordination run."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    return {"run": run.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.get("/runs")
async def list_coordination_runs(
    request: Request,
    root_session_id: str | None = Query(None, description="Filter by root conversation"),
) -> dict[str, Any]:
    """List coordination runs."""
    if root_session_id:
        await _require_coordination_tree(request, root_session_id)
    store = _request_store(request)
    runs = await asyncio.to_thread(store.list_runs, root_session_id)
    return {"runs": [r.to_dict() for r in runs]}


# ── Task Endpoints ────────────────────────────────────────────


@router.post("/tasks")
async def create_coordination_task(req: CreateTaskRequest, request: Request) -> dict[str, Any]:
    """Add a task to a coordination run."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, req.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {req.run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    task = CoordinationTask(
        run_id=req.run_id,
        title=req.title,
        assignee_session_id=req.assignee_session_id,
        assignee_role=req.assignee_role,
        dependencies=req.dependencies,
        artifacts=req.artifacts,
    )
    created = await asyncio.to_thread(store.create_task, task)
    return {"task": created.to_dict()}


@router.get("/tasks")
async def list_coordination_tasks(
    request: Request,
    run_id: str = Query(..., description="Run ID"),
) -> dict[str, Any]:
    """List all tasks associated with a coordination run."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    return {"tasks": [t.to_dict() for t in tasks]}


# ── Workflows DAG Execution ───────────────────────────────────


@router.post("/workflows/plan-implement-review")
async def start_plan_implement_review_workflow(
    req: StartWorkflowRequest, request: Request
) -> dict[str, Any]:
    """Kick off an end-to-end Plan -> Implement -> Review -> Fix -> Test run."""
    await _require_coordination_tree(
        request,
        req.root_session_id,
        req.planner_session_id,
        req.implementer_session_id,
        req.reviewer_session_id,
    )
    engine = _request_workflow_engine(request)
    store = _request_store(request)
    run = await engine.start_plan_implement_review_run(
        title=req.title,
        root_session_id=req.root_session_id,
        planner_session_id=req.planner_session_id,
        implementer_session_id=req.implementer_session_id,
        reviewer_session_id=req.reviewer_session_id,
        user_prompt=req.user_prompt,
        workspace_path=req.workspace_path,
    )
    tasks = await asyncio.to_thread(store.list_tasks, run.run_id)
    return {"run": run.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/workflows/{run_id}/tasks/{task_id}/advance")
async def advance_coordination_workflow(
    run_id: str,
    task_id: str,
    req: AdvanceWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    """Report one stage's result and advance the resumed workflow machine."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    engine = _request_workflow_engine(request)
    updated = await engine.advance(
        run_id=run_id,
        task_id=task_id,
        outcome=req.outcome,
        artifacts=req.artifacts,
        review_decision=req.review_decision,
    )
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    return {"run": updated.to_dict(), "tasks": [t.to_dict() for t in tasks]}


# ── Workspace Lease & Merge Preview ───────────────────────────


@router.post("/workspaces/lease")
async def acquire_workspace_lease(req: AcquireLeaseRequest, request: Request) -> dict[str, Any]:
    """Acquire a concurrency lease on a workspace path to prevent conflicting writes."""
    await _require_coordination_tree(
        request, req.root_session_id, req.holder_session_id
    )
    lease_mgr = _request_lease_manager(request)
    try:
        lease = lease_mgr.acquire(
            workspace_path=req.workspace_path,
            holder_session_id=req.holder_session_id,
            mode="write" if req.mode == "write" else "read",
            duration_s=req.duration_s,
        )
        return {"lease": lease.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/workspaces/merge-preview")
async def get_merge_preview(
    request: Request,
    repo_path: str = Query(..., description="Local repo root path"),
    source_branch: str = Query(..., description="Worktree source branch"),
    target_branch: str = Query("main", description="Target merge branch"),
) -> dict[str, Any]:
    """Generate merge preview diff and statistics."""
    coordinator = _request_workspace_coord(request)
    return await asyncio.to_thread(
        coordinator.generate_merge_preview, repo_path, source_branch, target_branch
    )


# ── Timeline & Audit Events ───────────────────────────────────


@router.get("/events")
async def list_coordination_events(
    request: Request,
    root_session_id: str = Query(..., description="The parent/root conversation id"),
    since: float | None = Query(None, description="Timestamp filter for cursor pagination"),
) -> dict[str, Any]:
    """Retrieve coordination timeline events."""
    await _require_coordination_tree(request, root_session_id)
    store = _request_store(request)
    events = await asyncio.to_thread(store.list_events, root_session_id, since)
    return {"events": [e.to_dict() for e in events]}
