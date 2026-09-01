"""FastAPI REST routes for AgentNexus Multi-Agent Coordination Control Plane (/v1/coordination)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from omnigent.coordination.behavior import (
    resolve_behavior_pack,
    session_behavior_mode_from_labels,
)
from omnigent.coordination.limits import (
    DEFAULT_MAX_HOPS,
    CoordinationLimitError,
    validate_message_envelope,
)
from omnigent.coordination.policy_gate import (
    CoordinationPolicyDecision,
    CoordinationPolicyGate,
)
from omnigent.coordination.store import CoordinationStore, get_default_coordination_db_path
from omnigent.coordination.types import (
    AgentMessage,
    ArtifactKind,
    CoordinationArtifact,
    CoordinationEvent,
    CoordinationRun,
    CoordinationTask,
    MessageKind,
)
from omnigent.coordination.workflow_engine import CoordinationWorkflowEngine, WorkflowDagTaskSpec
from omnigent.debug_logging import current_user_id
from omnigent.runtime import get_agent_cache, get_caps, get_policy_store
from omnigent.runtime.policies.builder import (
    build_default_policy_engine,
    build_session_policy_engine,
)
from omnigent.server.auth import LEVEL_MANAGE, LEVEL_READ
from omnigent.server.routes._auth_helpers import require_access as _require_access
from omnigent.server.routes._coordination_workspace import (
    CoordinationWorkspaceError,
    canonical_workspace_path,
    is_path_within,
    managed_workspace_boundaries,
)
from omnigent.spec.types import Phase, PolicyAction
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


async def _load_agent_spec_for_coordination(
    request: Request,
    conversation: Any,
) -> Any:
    """Load the parsed agent spec for a coordination session, if bound."""
    agent_store = getattr(request.app.state, "agent_store", None)
    if agent_store is None or getattr(conversation, "agent_id", None) is None:
        return None
    agent = await asyncio.to_thread(agent_store.get, conversation.agent_id)
    if agent is None:
        return None
    agent_cache = get_agent_cache()
    loaded = await asyncio.to_thread(
        lambda: agent_cache.load(
            agent.id,
            agent.bundle_location,
            expand_env=agent.session_id is None,
        )
    )
    return loaded.spec


def _request_coordination_policy_gate(request: Request) -> CoordinationPolicyGate | None:
    """Resolve the policy gate for one coordination request.

    Tests and embedded apps can bind ``app.state.coordination_policy_gate``
    to a deterministic gate. Production uses runtime stores and builds
    stage-scoped engines lazily.
    """
    override = getattr(request.app.state, "coordination_policy_gate", None)
    if override is not None:
        return override
    conversation_store = getattr(request.app.state, "conversation_store", None)
    if conversation_store is None:
        return None
    agent_store = getattr(request.app.state, "agent_store", None)
    policy_store = get_policy_store() or getattr(request.app.state, "policy_store", None)
    if agent_store is None and policy_store is None:
        return None
    caps = get_caps()
    server_llm = caps.llm
    host_connection = (
        caps.policy_llm_connection_factory()
        if caps.policy_llm_connection_factory
        else None
    )
    default_policies = getattr(caps, "default_policies", None)

    async def engine_factory(stage: str, session_id: str):
        if stage == "server_default":
            return await asyncio.to_thread(
                build_default_policy_engine,
                conversation_id=session_id,
                conversation_store=conversation_store,
                default_policies=default_policies,
                policy_store=policy_store,
                server_llm=server_llm,
                host_connection=host_connection,
            )
        conversation = await asyncio.to_thread(
            conversation_store.get_conversation, session_id
        )
        if conversation is None:
            return None
        spec = await _load_agent_spec_for_coordination(request, conversation)
        return await asyncio.to_thread(
            build_session_policy_engine,
            conversation_id=session_id,
            conversation_store=conversation_store,
            conversation=conversation,
            spec=spec,
            policy_store=policy_store,
            server_llm=server_llm,
            host_connection=host_connection,
        )

    return CoordinationPolicyGate(engine_factory)


async def _evaluate_coordination_policy(
    request: Request,
    *,
    phase: Phase,
    root_session_id: str,
    content: dict[str, Any],
    source_session_id: str | None = None,
    target_session_id: str | None = None,
    run_id: str | None = None,
    tool_name: str | None = None,
    actor_session_id: str | None = None,
) -> CoordinationPolicyDecision | None:
    """Run the §14.2 policy pipeline and record an audit event."""
    gate = _request_coordination_policy_gate(request)
    if gate is None:
        return None
    decision = await gate.evaluate(
        phase=phase,
        root_session_id=root_session_id,
        content=content,
        source_session_id=source_session_id,
        target_session_id=target_session_id,
        run_id=run_id,
        tool_name=tool_name,
    )
    store = _request_store(request)
    event = CoordinationEvent(
        root_session_id=root_session_id,
        run_id=run_id,
        actor_session_id=actor_session_id,
        event_type=f"policy.{decision.action.value}.{phase.value}",
        payload={
            "stage": decision.stage,
            "reason": decision.reason,
            "deciding_policies": list(decision.deciding_policies or []),
            "required_acl_level": decision.required_acl_level,
        },
    )
    await asyncio.to_thread(store.record_event, event)
    if not decision.allowed:
        detail = (
            decision.reason
            or f"coordination {phase.value} denied at {decision.stage or 'policy'}"
        )
        if decision.action == PolicyAction.ASK:
            detail = (
                f"{detail}; explicit {decision.required_acl_level or 'manage'} "
                "approval is required and was not granted"
            )
        raise HTTPException(status_code=403, detail=detail)
    return decision


async def _require_coordination_acl(
    request: Request,
    root_session_id: str,
    *session_ids: str,
) -> None:
    """Apply the same session ACL used by the rest of the API.

    Read-only coordination endpoints require ``LEVEL_READ``; mutating control
    endpoints require ``LEVEL_MANAGE`` so shared read/edit users cannot rewrite
    agent identity, advance runs, or issue lease/merge commands. The check runs
    against the root and every sender/recipient/holder named by the request,
    with sub-agent delegation handled by the shared session-access checker.
    """
    permission_store = getattr(request.app.state, "permission_store", None)
    if permission_store is None:
        return
    conversation_store = getattr(request.app.state, "conversation_store", None)
    if conversation_store is None:
        raise HTTPException(
            status_code=503,
            detail="coordination routes require a conversation store",
        )
    required_level = (
        LEVEL_MANAGE if request.method in {"POST", "PATCH", "PUT", "DELETE"} else LEVEL_READ
    )
    await _require_access(
        current_user_id(),
        root_session_id,
        required_level,
        permission_store,
        conversation_store,
    )
    for session_id in session_ids:
        await _require_access(
            current_user_id(),
            session_id,
            required_level,
            permission_store,
            conversation_store,
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
    await _require_coordination_acl(
        request,
        root_session_id,
        *session_ids,
    )


async def _require_managed_workspace_path(
    request: Request,
    *,
    root_session_id: str,
    holder_session_id: str,
    requested_path: str,
    field_name: str,
) -> str:
    """Fail closed unless ``requested_path`` is a managed session workspace.

    Coordination lease/merge operations only touch paths that belong to
    host-launched sessions in the coordination tree. The holder's workspace is
    the primary boundary and the root's workspace the secondary boundary, so a
    merge on the source repo remains valid for agents running in sibling
    worktrees. Any unmanaged, cross-host, or outside-boundary path is rejected.
    """
    store = getattr(request.app.state, "conversation_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="coordination routes require a conversation store",
        )
    root = await asyncio.to_thread(store.get_conversation, root_session_id)
    holder = await asyncio.to_thread(store.get_conversation, holder_session_id)
    if root is None or holder is None:
        raise HTTPException(status_code=404, detail="session not found")

    try:
        canonical = canonical_workspace_path(requested_path)
    except CoordinationWorkspaceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    host_id, boundaries = managed_workspace_boundaries(root, holder)
    if not host_id or not boundaries:
        raise HTTPException(
            status_code=403,
            detail=(
                "workspace operations require a host-managed session with a "
                "recorded workspace; this session tree has none"
            ),
        )

    root_host = getattr(root, "host_id", None)
    holder_host = getattr(holder, "host_id", None)
    if root_host and holder_host and root_host != holder_host:
        raise HTTPException(
            status_code=403,
            detail="cross-host coordination trees cannot share workspace operations",
        )

    if not any(is_path_within(canonical, boundary) for boundary in boundaries):
        raise HTTPException(
            status_code=403,
            detail=(
                f"{field_name} {requested_path!r} is outside the managed "
                "workspaces of this session tree"
            ),
        )
    return canonical


# ── Pydantic Request / Response Models ────────────────────────


class SendMessageRequest(BaseModel):
    root_session_id: str
    sender_session_id: str
    recipient_session_id: str
    sender_role: str = "general"
    recipient_role: str | None = None
    kind: MessageKind = "content"
    intent: str = "task.request"
    run_id: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    correlation_id: str | None = None
    in_reply_to: str | None = None
    idempotency_key: str | None = None
    hop_count: int = 0
    max_hops: int = DEFAULT_MAX_HOPS
    ttl_seconds: float | None = None


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
    acceptance_criteria: list[str] = Field(default_factory=list)
    deadline: float | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class StartWorkflowRequest(BaseModel):
    title: str = "Plan -> Implement -> Review Workflow"
    root_session_id: str
    planner_session_id: str
    implementer_session_id: str
    reviewer_session_id: str
    user_prompt: str
    workspace_path: str = "."
    budget: dict[str, Any] = Field(default_factory=dict)
    behavior_modes: dict[str, Literal["off", "advisory", "lean", "strict"]] = Field(
        default_factory=dict
    )


class AdvanceWorkflowRequest(BaseModel):
    outcome: Literal["succeeded", "failed"] = "succeeded"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    review_decision: str | None = None


class ReportWorkflowTaskRequest(BaseModel):
    actor_session_id: str
    outcome: Literal["succeeded", "failed"] = "succeeded"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    review_decision: str | None = None
    summary: str | None = None


class StartTemplateWorkflowTask(BaseModel):
    name: str
    title: str
    assignee_session_id: str
    assignee_role: str
    prompt: str
    intent: str = "task.request"
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    behavior_mode: Literal["off", "advisory", "lean", "strict"] | None = None


class StartTemplateWorkflowRequest(BaseModel):
    title: str = "Custom DAG Workflow"
    root_session_id: str
    tasks: list[StartTemplateWorkflowTask]
    budget: dict[str, Any] = Field(default_factory=dict)


class AcquireLeaseRequest(BaseModel):
    root_session_id: str
    workspace_path: str
    holder_session_id: str
    mode: str = "write"
    duration_s: float = 600.0


def _reject_dag_cycles(tasks: list[StartTemplateWorkflowTask]) -> None:
    """Raise HTTP 400 when the caller-supplied workflow graph has a cycle."""
    indegree: dict[str, int] = {task.name: 0 for task in tasks}
    dependents: dict[str, list[str]] = {task.name: [] for task in tasks}
    for task in tasks:
        for dep in task.dependencies:
            indegree[task.name] += 1
            dependents[dep].append(task.name)
    ready = [name for name, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        name = ready.pop()
        visited += 1
        for dependent in dependents[name]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if visited != len(tasks):
        cyclic = [name for name, degree in indegree.items() if degree > 0]
        raise HTTPException(
            status_code=400,
            detail=f"dag tasks form a cycle involving {cyclic}",
        )


class CreateMergePreviewRequest(BaseModel):
    root_session_id: str
    holder_session_id: str
    repo_path: str
    source_branch: str
    target_branch: str = "main"


class ExecuteMergeRequest(BaseModel):
    fencing_token: int


class CreateArtifactRequest(BaseModel):
    root_session_id: str
    producer_session_id: str
    run_id: str | None = None
    task_id: str | None = None
    kind: ArtifactKind = "other"
    digest: str | None = None
    uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateArtifactStatusRequest(BaseModel):
    status: Literal["published", "updated", "invalidated"]


class ReportConsumptionRequest(BaseModel):
    state: Literal["consumed", "acknowledged", "rejected"]
    actor_session_id: str
    receipt: dict[str, Any] = Field(default_factory=dict)


class ReassignTaskRequest(BaseModel):
    assignee_session_id: str


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
    try:
        validate_message_envelope(
            hop_count=req.hop_count,
            max_hops=req.max_hops,
            ttl_seconds=req.ttl_seconds,
            payload=req.payload,
            artifacts=req.artifacts,
        )
    except CoordinationLimitError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    payload_preview = str(req.payload)
    await _evaluate_coordination_policy(
        request,
        phase=Phase.COORDINATION_MESSAGE,
        root_session_id=req.root_session_id,
        source_session_id=req.sender_session_id,
        target_session_id=req.recipient_session_id,
        run_id=req.run_id,
        tool_name="coordination.message",
        actor_session_id=req.sender_session_id,
        content={
            "sender": req.sender_session_id,
            "recipient": req.recipient_session_id,
            "kind": req.kind,
            "intent": req.intent,
            "payload_size": len(payload_preview),
            "payload_preview": payload_preview[:4096],
            "artifacts": [
                a.get("artifact_id") or a.get("uri") for a in req.artifacts
            ],
            "hop_count": req.hop_count,
            "max_hops": req.max_hops,
            "ttl_seconds": req.ttl_seconds,
        },
    )
    msg = AgentMessage(
        root_session_id=req.root_session_id,
        run_id=req.run_id,
        task_id=req.task_id,
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
        hop_count=req.hop_count,
        max_hops=req.max_hops,
        ttl_seconds=req.ttl_seconds,
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


@router.get("/behavior/{session_id}")
async def get_session_behavior_facts(
    session_id: str,
    request: Request,
    root_session_id: str = Query(..., description="The parent/root conversation id"),
) -> dict[str, Any]:
    """Return the latest durable Behavior binding addressed to one session.

    The Inspector badge is only honest when it distinguishes a composed
    binding from an actually confirmed injection. ``delivery_state`` comes
    from the latest DeliveryAttempt (or the queued message when the outbox
    has not picked it up yet), so an unconfirmed payload never renders as
    active.
    """
    await _require_coordination_tree(request, root_session_id, session_id)
    store = _request_store(request)
    messages = await asyncio.to_thread(store.list_messages, root_session_id, session_id)
    bound = next(
        (m for m in reversed(messages) if "behavior_binding" in m.payload),
        None,
    )
    conversation_store = getattr(request.app.state, "conversation_store", None)
    session_mode: dict[str, Any] | None = None
    if conversation_store is not None:
        conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conv is not None:
            requested = session_behavior_mode_from_labels(getattr(conv, "labels", None))
            if requested is not None:
                session_mode = resolve_behavior_pack(
                    user_mode=requested,
                    scope="session",
                ).to_dict()
    if bound is None:
        if session_mode is not None:
            return {
                "session_id": session_id,
                "binding": None,
                "session_mode": session_mode,
                "message_id": None,
                "delivery_state": "none",
                "consumption_state": "none",
                "reason": "session_mode_not_yet_injected",
            }
        return {
            "session_id": session_id,
            "binding": None,
            "session_mode": None,
            "message_id": None,
            "delivery_state": "none",
            "consumption_state": "none",
            "reason": "no_workflow_behavior_binding",
        }
    attempts = await asyncio.to_thread(store.list_delivery_attempts, bound.message_id)
    latest = attempts[-1] if attempts else None
    delivery_state = (
        latest.delivery_state
        if latest is not None
        else ("queued" if bound.message_state == "queued" else "unknown")
    )
    return {
        "session_id": session_id,
        "binding": bound.payload["behavior_binding"],
        "session_mode": session_mode,
        "message_id": bound.message_id,
        "delivery_state": delivery_state,
        "consumption_state": bound.consumption_state,
        "reason": (
            "confirmed_injection"
            if latest is not None and latest.delivery_state == "confirmed"
            else "recorded_pending_delivery"
        ),
    }


@router.post("/messages/{message_id}/cancel")
async def cancel_coordination_message(
    message_id: str,
    request: Request,
) -> dict[str, Any]:
    """Cancel a still-queued peer message before it is injected."""
    store = _request_store(request)
    message = await asyncio.to_thread(store.get_message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail=f"Message {message_id} not found")
    await _require_coordination_tree(request, message.root_session_id, message.sender_session_id)
    if message.message_state in ("cancelled", "expired"):
        return {"message": message.to_dict(), "cancelled": True}
    if message.message_state != "queued":
        raise HTTPException(
            status_code=409,
            detail=(
                f"message has state {message.message_state}; only queued messages can be cancelled"
            ),
        )
    cancelled = await asyncio.to_thread(store.cancel_message, message_id)
    event = CoordinationEvent(
        root_session_id=message.root_session_id,
        run_id=message.run_id,
        task_id=message.task_id,
        actor_session_id=message.sender_session_id,
        event_type="message.cancelled",
        payload={"message_id": message_id},
    )
    await asyncio.to_thread(store.record_event, event)
    return {"message": cancelled.to_dict() if cancelled else None, "cancelled": True}


@router.post("/messages/{message_id}/receipt")
async def report_message_consumption(
    message_id: str,
    req: ReportConsumptionRequest,
    request: Request,
) -> dict[str, Any]:
    """Record an explicit consumption/ack/reject receipt from the recipient agent."""
    store = _request_store(request)
    message = await asyncio.to_thread(store.get_message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail=f"Message {message_id} not found")
    if req.actor_session_id != message.recipient_session_id:
        raise HTTPException(
            status_code=403,
            detail="only the recipient agent can report consumption",
        )
    await _require_coordination_tree(request, message.root_session_id, req.actor_session_id)
    if message.consumption_state == req.state and message.consumption_state != "unconsumed":
        return {"message": message.to_dict()}
    if message.message_state != "active":
        raise HTTPException(
            status_code=409,
            detail=(
                f"message has state {message.message_state}; "
                "a receipt requires an actively delivered message"
            ),
        )
    updated = await asyncio.to_thread(
        store.record_consumption_receipt,
        message_id,
        req.state,
        req.receipt,
    )
    event = CoordinationEvent(
        root_session_id=message.root_session_id,
        run_id=message.run_id,
        task_id=message.task_id,
        actor_session_id=req.actor_session_id,
        event_type=f"message.{req.state}",
        payload={"message_id": message_id, "receipt": req.receipt},
    )
    await asyncio.to_thread(store.record_event, event)
    return {"message": updated.to_dict() if updated else None}


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


# ── Run Summary ──────────────────────────────────────────────


@router.get("/runs/{run_id}/summary")
async def get_coordination_run_summary(
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the durable run, stage board, and delivery/artifact summary."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    messages = await asyncio.to_thread(store.list_messages, run.root_session_id)
    artifacts = await asyncio.to_thread(store.list_artifacts, run.root_session_id, run_id=run_id)
    task_statuses: dict[str, str] = {}
    for task in tasks:
        role = task.assignee_role or "unassigned"
        task_statuses[role] = task.status
    message_states: dict[str, int] = {}
    consumption_states: dict[str, int] = {}
    effect_unknown_count = 0
    for message in messages:
        if message.run_id != run_id:
            continue
        message_states[message.message_state] = message_states.get(message.message_state, 0) + 1
        consumption_states[message.consumption_state] = (
            consumption_states.get(message.consumption_state, 0) + 1
        )
        if message.effect_unknown_reason is not None:
            effect_unknown_count += 1
    return {
        "run": run.to_dict(),
        "summary": {
            "template_version": run.metadata.get("template_version", "unknown"),
            "stage": task_statuses,
            "retry_count": int(run.metadata.get("retry_count") or 0),
            "fix_cycles": int(run.metadata.get("fix_cycles") or 0),
            "message_states": message_states,
            "consumption_states": consumption_states,
            "effect_unknown_count": effect_unknown_count,
            "artifact_count": len(artifacts),
        },
    }


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
        acceptance_criteria=req.acceptance_criteria,
        deadline=req.deadline,
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
        budget=req.budget,
        behavior_modes=req.behavior_modes,
    )
    tasks = await asyncio.to_thread(store.list_tasks, run.run_id)
    return {"run": run.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/workflows/template")
async def start_template_workflow(
    req: StartTemplateWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    """Start a caller-defined task DAG (template workflow).

    ``tasks`` define stage keys, assignee sessions, prompts and dependency
    keys. Root stages are dispatched immediately; every other stage starts as
    soon as its dependencies have succeeded, using the same durable outbox,
    consumption receipts and report/retry/reassign endpoint as the fixed
    workflow. Cycles, duplicate keys and unknown dependency keys are rejected.
    """
    names = [task.name for task in req.tasks]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="duplicate dag task names")
    known = set(names)
    for task in req.tasks:
        unknown = [dep for dep in task.dependencies if dep not in known]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"task {task.name!r} depends on unknown {unknown}",
            )
    _reject_dag_cycles(req.tasks)
    await _require_coordination_tree(
        request,
        req.root_session_id,
        *(task.assignee_session_id for task in req.tasks),
    )
    engine = _request_workflow_engine(request)
    store = _request_store(request)
    run = await engine.start_dag_workflow_run(
        title=req.title,
        root_session_id=req.root_session_id,
        tasks=[
            WorkflowDagTaskSpec(
                name=task.name,
                title=task.title,
                assignee_session_id=task.assignee_session_id,
                assignee_role=task.assignee_role,
                prompt=task.prompt,
                intent=task.intent,
                acceptance_criteria=task.acceptance_criteria,
                dependencies=task.dependencies,
                behavior_mode=task.behavior_mode,
            )
            for task in req.tasks
        ],
        budget=req.budget,
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
    try:
        updated = await engine.advance(
            run_id=run_id,
            task_id=task_id,
            outcome=req.outcome,
            artifacts=req.artifacts,
            review_decision=req.review_decision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    return {"run": updated.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/workflows/{run_id}/tasks/{task_id}/report")
async def report_workflow_task_result(
    run_id: str,
    task_id: str,
    req: ReportWorkflowTaskRequest,
    request: Request,
) -> dict[str, Any]:
    """Record a stage result as a durable message, then advance the workflow.

    The result is persisted through the same AgentMessage/Outbox path as an
    ordinary peer message, so the Control Plane keeps an auditable correlation
    between the stage task, the result, and the next dispatch. Only the task's
    assigned session may report; the root orchestrator still controls pause,
    retry, reassign, and cancel through the existing run endpoints.
    """
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    task = await asyncio.to_thread(store.get_task, task_id)
    if task is None or task.run_id != run_id:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in run {run_id}")
    await _require_coordination_tree(
        request,
        run.root_session_id,
        req.actor_session_id,
    )
    if task.assignee_session_id != req.actor_session_id:
        raise HTTPException(
            status_code=403,
            detail=(f"session {req.actor_session_id!r} is not assigned to task {task_id!r}"),
        )

    result_msg = AgentMessage(
        root_session_id=run.root_session_id,
        run_id=run.run_id,
        task_id=task.task_id,
        sender_session_id=req.actor_session_id,
        sender_role=task.assignee_role or "agent",
        recipient_session_id=run.root_session_id,
        recipient_role="user_orchestrator",
        kind="event",
        intent="task.result",
        payload={
            "outcome": req.outcome,
            "review_decision": req.review_decision,
            "summary": req.summary,
        },
        artifacts=req.artifacts,
        correlation_id=f"workflow:{run.run_id}:task:{task.task_id}",
    )
    saved, _outbox = await asyncio.to_thread(store.save_message_and_outbox, result_msg)
    engine = _request_workflow_engine(request)
    try:
        updated = await engine.advance(
            run_id=run.run_id,
            task_id=task.task_id,
            outcome=req.outcome,
            artifacts=req.artifacts,
            review_decision=req.review_decision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    tasks = await asyncio.to_thread(store.list_tasks, run.run_id)
    return {
        "message": saved.to_dict(),
        "run": updated.to_dict(),
        "tasks": [t.to_dict() for t in tasks],
    }


@router.post("/runs/{run_id}/pause")
async def pause_coordination_run(run_id: str, request: Request) -> dict[str, Any]:
    """Pause a running coordination run; queued stages stay queued."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    updated = await _request_workflow_engine(request).pause_run(run_id)
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    return {"run": updated.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/runs/{run_id}/resume")
async def resume_coordination_run(run_id: str, request: Request) -> dict[str, Any]:
    """Resume a paused coordination run."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    updated = await _request_workflow_engine(request).resume_run(run_id)
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    return {"run": updated.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/runs/{run_id}/cancel")
async def cancel_coordination_run(run_id: str, request: Request) -> dict[str, Any]:
    """Cancel a run: queued deliveries cancel, active unconsumed are rejected."""
    store = _request_store(request)
    run = await asyncio.to_thread(store.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    updated = await _request_workflow_engine(request).cancel_run(run_id)
    tasks = await asyncio.to_thread(store.list_tasks, run_id)
    return {"run": updated.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/tasks/{task_id}/retry")
async def retry_coordination_task(task_id: str, request: Request) -> dict[str, Any]:
    """Retry a failed stage task on its original run/task identity."""
    store = _request_store(request)
    task = await asyncio.to_thread(store.get_task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    run = await asyncio.to_thread(store.get_run, task.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {task.run_id} not found")
    await _require_coordination_tree(request, run.root_session_id)
    try:
        updated = await _request_workflow_engine(request).retry_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    tasks = await asyncio.to_thread(store.list_tasks, run.run_id)
    return {"run": updated.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/tasks/{task_id}/reassign")
async def reassign_coordination_task(
    task_id: str,
    req: ReassignTaskRequest,
    request: Request,
) -> dict[str, Any]:
    """Redirect an active stage task to another assignee in the same tree."""
    store = _request_store(request)
    task = await asyncio.to_thread(store.get_task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    run = await asyncio.to_thread(store.get_run, task.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {task.run_id} not found")
    await _require_coordination_tree(request, run.root_session_id, req.assignee_session_id)
    try:
        updated, changed = await _request_workflow_engine(request).reassign_task(
            task_id, req.assignee_session_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    tasks = await asyncio.to_thread(store.list_tasks, run.run_id)
    return {
        "run": updated.to_dict(),
        "tasks": [t.to_dict() for t in tasks],
        "reassigned": changed,
    }


# ── Workspace Lease & Merge Preview ───────────────────────────


@router.post("/workspaces/lease")
async def acquire_workspace_lease(req: AcquireLeaseRequest, request: Request) -> dict[str, Any]:
    """Acquire a concurrency lease on a workspace path to prevent conflicting writes."""
    await _require_coordination_tree(request, req.root_session_id, req.holder_session_id)
    canonical_path = await _require_managed_workspace_path(
        request,
        root_session_id=req.root_session_id,
        holder_session_id=req.holder_session_id,
        requested_path=req.workspace_path,
        field_name="workspace_path",
    )
    await _evaluate_coordination_policy(
        request,
        phase=Phase.WORKSPACE_OPERATION,
        root_session_id=req.root_session_id,
        source_session_id=req.holder_session_id,
        target_session_id=req.holder_session_id,
        tool_name="coordination.workspace.lease",
        actor_session_id=req.holder_session_id,
        content={
            "workspace_path": canonical_path,
            "mode": req.mode,
            "duration_s": req.duration_s,
            "holder": req.holder_session_id,
        },
    )
    lease_mgr = _request_lease_manager(request)
    try:
        lease = lease_mgr.acquire(
            workspace_path=canonical_path,
            holder_session_id=req.holder_session_id,
            mode="write" if req.mode == "write" else "read",
            duration_s=req.duration_s,
        )
        return {"lease": lease.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/workspaces/merge-previews")
async def create_merge_preview(
    req: CreateMergePreviewRequest,
    request: Request,
) -> dict[str, Any]:
    """Capture branch heads and dirty state for a user-confirmable merge operation."""
    await _require_coordination_tree(request, req.root_session_id, req.holder_session_id)
    canonical_repo_path = await _require_managed_workspace_path(
        request,
        root_session_id=req.root_session_id,
        holder_session_id=req.holder_session_id,
        requested_path=req.repo_path,
        field_name="repo_path",
    )
    await _evaluate_coordination_policy(
        request,
        phase=Phase.GIT_MERGE,
        root_session_id=req.root_session_id,
        source_session_id=req.holder_session_id,
        target_session_id=req.holder_session_id,
        tool_name="coordination.git.merge_preview",
        actor_session_id=req.holder_session_id,
        content={
            "repo_path": canonical_repo_path,
            "source_branch": req.source_branch,
            "target_branch": req.target_branch,
            "holder": req.holder_session_id,
        },
    )
    coordinator = _request_workspace_coord(request)
    store = _request_store(request)
    try:
        operation = await asyncio.to_thread(
            coordinator.prepare_merge_preview,
            store,
            root_session_id=req.root_session_id,
            holder_session_id=req.holder_session_id,
            repo_path=canonical_repo_path,
            source_branch=req.source_branch,
            target_branch=req.target_branch,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    event = CoordinationEvent(
        root_session_id=req.root_session_id,
        actor_session_id=req.holder_session_id,
        event_type="workspace.merge_preview.created",
        payload={
            "operation_id": operation.operation_id,
            "repo_path": operation.repo_path,
            "source_branch": operation.source_branch,
            "target_branch": operation.target_branch,
        },
    )
    await asyncio.to_thread(store.record_event, event)
    return {"operation": operation.to_dict()}


@router.get("/workspaces/merge-previews")
async def list_merge_previews(
    request: Request,
    root_session_id: str = Query(..., description="The parent/root conversation id"),
) -> dict[str, Any]:
    """List merge operations visible to a coordination root."""
    await _require_coordination_tree(request, root_session_id)
    store = _request_store(request)
    operations = await asyncio.to_thread(store.list_merge_operations, root_session_id)
    return {"operations": [o.to_dict() for o in operations]}


@router.get("/workspaces/merge-previews/{operation_id}")
async def get_merge_preview(
    operation_id: str,
    request: Request,
) -> dict[str, Any]:
    """Retrieve one merge operation by id."""
    store = _request_store(request)
    operation = await asyncio.to_thread(store.get_merge_operation, operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail=f"Merge operation {operation_id} not found")
    await _require_coordination_tree(
        request, operation.root_session_id, operation.holder_session_id
    )
    return {"operation": operation.to_dict()}


@router.post("/workspaces/merge-previews/{operation_id}/execute")
async def execute_merge_preview(
    operation_id: str,
    req: ExecuteMergeRequest,
    request: Request,
) -> dict[str, Any]:
    """Execute a previewed merge after re-validating lease, heads and dirty state."""
    store = _request_store(request)
    operation = await asyncio.to_thread(store.get_merge_operation, operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail=f"Merge operation {operation_id} not found")
    await _require_coordination_tree(
        request, operation.root_session_id, operation.holder_session_id
    )
    await _evaluate_coordination_policy(
        request,
        phase=Phase.GIT_MERGE,
        root_session_id=operation.root_session_id,
        source_session_id=operation.holder_session_id,
        target_session_id=operation.holder_session_id,
        tool_name="coordination.git.merge_execute",
        actor_session_id=operation.holder_session_id,
        content={
            "repo_path": operation.repo_path,
            "source_branch": operation.source_branch,
            "target_branch": operation.target_branch,
            "operation_id": operation_id,
            "fencing_token": req.fencing_token,
            "holder": operation.holder_session_id,
        },
    )
    coordinator = _request_workspace_coord(request)
    try:
        updated = await asyncio.to_thread(
            coordinator.execute_merge,
            store,
            operation_id=operation_id,
            fencing_token=req.fencing_token,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    event = CoordinationEvent(
        root_session_id=operation.root_session_id,
        actor_session_id=operation.holder_session_id,
        event_type=f"workspace.merge.{updated.status}",
        payload={
            "operation_id": operation_id,
            "source_branch": operation.source_branch,
            "target_branch": operation.target_branch,
        },
    )
    await asyncio.to_thread(store.record_event, event)
    return {"operation": updated.to_dict()}


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


# ── Artifact Metadata ─────────────────────────────────────────


@router.post("/artifacts")
async def create_coordination_artifact(
    req: CreateArtifactRequest,
    request: Request,
) -> dict[str, Any]:
    """Publish a control-plane artifact metadata row for a plan/patch/diff/report."""
    await _require_coordination_tree(request, req.root_session_id, req.producer_session_id)
    artifact = CoordinationArtifact(
        root_session_id=req.root_session_id,
        run_id=req.run_id,
        task_id=req.task_id,
        producer_session_id=req.producer_session_id,
        kind=req.kind,
        digest=req.digest,
        uri=req.uri,
        metadata=req.metadata,
    )
    store = _request_store(request)
    created = await asyncio.to_thread(store.create_artifact, artifact)
    event = CoordinationEvent(
        root_session_id=req.root_session_id,
        run_id=req.run_id,
        task_id=req.task_id,
        actor_session_id=req.producer_session_id,
        event_type="artifact.published",
        payload={
            "artifact_id": artifact.artifact_id,
            "kind": artifact.kind,
            "uri": artifact.uri,
        },
    )
    await asyncio.to_thread(store.record_event, event)
    return {"artifact": created.to_dict()}


@router.get("/artifacts")
async def list_coordination_artifacts(
    request: Request,
    root_session_id: str = Query(..., description="The parent/root conversation id"),
    run_id: str | None = Query(None, description="Optional run filter"),
    task_id: str | None = Query(None, description="Optional task filter"),
) -> dict[str, Any]:
    """List artifact metadata within a coordination root."""
    await _require_coordination_tree(request, root_session_id)
    store = _request_store(request)
    artifacts = await asyncio.to_thread(
        store.list_artifacts,
        root_session_id,
        run_id=run_id,
        task_id=task_id,
    )
    return {"artifacts": [a.to_dict() for a in artifacts]}


@router.get("/artifacts/{artifact_id}")
async def get_coordination_artifact(
    artifact_id: str,
    request: Request,
) -> dict[str, Any]:
    """Retrieve one artifact metadata row by id."""
    store = _request_store(request)
    artifact = await asyncio.to_thread(store.get_artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    await _require_coordination_tree(
        request, artifact.root_session_id, artifact.producer_session_id
    )
    return {"artifact": artifact.to_dict()}


@router.patch("/artifacts/{artifact_id}")
async def update_coordination_artifact_status(
    artifact_id: str,
    req: UpdateArtifactStatusRequest,
    request: Request,
) -> dict[str, Any]:
    """Mark an artifact published/updated/invalidated."""
    store = _request_store(request)
    artifact = await asyncio.to_thread(store.get_artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    await _require_coordination_tree(
        request, artifact.root_session_id, artifact.producer_session_id
    )
    updated = await asyncio.to_thread(store.update_artifact_status, artifact_id, req.status)
    event = CoordinationEvent(
        root_session_id=artifact.root_session_id,
        run_id=artifact.run_id,
        task_id=artifact.task_id,
        actor_session_id=artifact.producer_session_id,
        event_type=f"artifact.{req.status}",
        payload={"artifact_id": artifact_id, "kind": artifact.kind},
    )
    await asyncio.to_thread(store.record_event, event)
    return {"artifact": updated.to_dict() if updated else None}
