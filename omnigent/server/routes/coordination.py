"""FastAPI REST routes for AgentNexus Multi-Agent Coordination Control Plane (/v1/coordination)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from omnigent.coordination.store import CoordinationStore, get_default_coordination_db_path
from omnigent.coordination.types import (
    AgentMessage,
    CoordinationRun,
    CoordinationTask,
    MessageKind,
    RunStatus,
    TaskStatus,
)

router = APIRouter(prefix="/v1/coordination", tags=["Coordination"])
_store = CoordinationStore(get_default_coordination_db_path())


def get_coordination_store() -> CoordinationStore:
    return _store


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


# ── Message Endpoints ─────────────────────────────────────────


@router.post("/messages")
async def send_coordination_message(req: SendMessageRequest) -> dict[str, Any]:
    """Send a durable peer message from one agent to another and queue for delivery."""
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
    saved_msg, outbox = await asyncio.to_thread(_store.save_message_and_outbox, msg)
    return {
        "message": saved_msg.to_dict(),
        "outbox_item_id": outbox.item_id,
        "delivery_state": outbox.status,
    }


@router.get("/messages")
async def list_coordination_messages(
    root_session_id: str = Query(..., description="The parent/root conversation id"),
    recipient_session_id: str | None = Query(None, description="Optional target session filter"),
) -> dict[str, Any]:
    """List peer messages within a coordination room/root session."""
    messages = await asyncio.to_thread(_store.list_messages, root_session_id, recipient_session_id)
    return {"messages": [m.to_dict() for m in messages]}


# ── Run Endpoints ─────────────────────────────────────────────


@router.post("/runs")
async def create_coordination_run(req: CreateRunRequest) -> dict[str, Any]:
    """Create a new multi-agent collaboration run."""
    run = CoordinationRun(
        title=req.title,
        root_session_id=req.root_session_id,
        template=req.template,
        budget=req.budget,
        metadata=req.metadata,
    )
    created = await asyncio.to_thread(_store.create_run, run)
    return {"run": created.to_dict()}


@router.get("/runs/{run_id}")
async def get_coordination_run(run_id: str) -> dict[str, Any]:
    """Retrieve details and tasks for a specific coordination run."""
    run = await asyncio.to_thread(_store.get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    tasks = await asyncio.to_thread(_store.list_tasks, run_id)
    return {"run": run.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.get("/runs")
async def list_coordination_runs(
    root_session_id: str | None = Query(None, description="Filter by root conversation"),
) -> dict[str, Any]:
    """List coordination runs."""
    runs = await asyncio.to_thread(_store.list_runs, root_session_id)
    return {"runs": [r.to_dict() for r in runs]}


# ── Task Endpoints ────────────────────────────────────────────


@router.post("/tasks")
async def create_coordination_task(req: CreateTaskRequest) -> dict[str, Any]:
    """Add a task to a coordination run."""
    task = CoordinationTask(
        run_id=req.run_id,
        title=req.title,
        assignee_session_id=req.assignee_session_id,
        assignee_role=req.assignee_role,
        dependencies=req.dependencies,
        artifacts=req.artifacts,
    )
    created = await asyncio.to_thread(_store.create_task, task)
    return {"task": created.to_dict()}


@router.get("/tasks")
async def list_coordination_tasks(run_id: str = Query(..., description="Run ID")) -> dict[str, Any]:
    """List all tasks associated with a coordination run."""
    tasks = await asyncio.to_thread(_store.list_tasks, run_id)
    return {"tasks": [t.to_dict() for t in tasks]}


# ── Timeline & Audit Events ───────────────────────────────────


@router.get("/events")
async def list_coordination_events(
    root_session_id: str = Query(..., description="The parent/root conversation id"),
    since: float | None = Query(None, description="Timestamp filter for cursor pagination"),
) -> dict[str, Any]:
    """Retrieve coordination timeline events."""
    events = await asyncio.to_thread(_store.list_events, root_session_id, since)
    return {"events": [e.to_dict() for e in events]}
