"""Domain types for Agent-to-Agent Coordination and Control Plane (coordination.v1)."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from agentnexus.event_catalog import classify_event_category

MessageKind = Literal["content", "command", "event"]
MessageState = Literal["queued", "active", "cancelled", "expired"]
DeliveryState = Literal["pending", "leased", "injected", "confirmed", "failed", "unknown"]
DeliveryMode = Literal["live", "breakpoint", "next_turn", "terminal_best_effort", "offline"]
ConsumptionState = Literal["unconsumed", "consumed", "acknowledged", "rejected"]
MergeOperationStatus = Literal["preview", "executing", "merged", "conflict", "failed", "cancelled"]
ArtifactKind = Literal["plan", "patch", "diff", "report", "test_result", "log", "other"]
ArtifactStatus = Literal["published", "updated", "invalidated"]
DEFAULT_MAX_HOPS = 8
DEFAULT_MAX_PAYLOAD_BYTES = 256 * 1024
DEFAULT_MAX_ARTIFACT_REFERENCES = 32
DEFAULT_MAX_TTL_SECONDS = 7 * 24 * 60 * 60

#: How long an outbox lease may stay ``leased`` before a recovery pass treats
#: the dispatching process as gone. Generous on purpose: injection is a single
#: HTTP call, but a loaded runner can take a while to answer.
OUTBOX_LEASE_TIMEOUT_S = 120.0

# Delivery error taxonomy. Every failure is persisted with one of these
# prefixes so a later recovery pass can separate "the runner provably never
# saw this request" (safe to replay) from "the request may have landed and
# started a turn" (must be escalated as effect-unknown). Errors written by
# older builds carry no prefix and are treated as unproven.
DELIVERY_ERROR_UNREACHABLE = "delivery-unreachable"
DELIVERY_ERROR_REJECTED = "delivery-rejected"
DELIVERY_ERROR_UNPROVEN = "delivery-unproven"

#: Prefixes that prove the request never reached the runner, so replaying it
#: cannot duplicate a side effect.
SAFE_TO_REPLAY_ERROR_PREFIXES = (
    f"{DELIVERY_ERROR_UNREACHABLE}:",
    f"{DELIVERY_ERROR_REJECTED}:",
)
RunStatus = Literal[
    "draft",
    "running",
    "paused",
    "waiting_user",
    "waiting_peer",
    "reconciling",
    "succeeded",
    "failed",
    "cancelled",
    "needs_attention",
]
TaskStatus = Literal[
    "draft",
    "queued",
    "assigned",
    "running",
    "blocked",
    "waiting_user",
    "waiting_peer",
    "waiting_review",
    "reconciling",
    "succeeded",
    "failed",
    "cancelled",
    "needs_attention",
]


def generate_coordination_id(prefix: str) -> str:
    """Generate a stable, human-readable identifier with the given prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class AgentMessage:
    """A durable, auditable peer message sent between agents (or user/orchestrator)."""

    message_id: str = field(default_factory=lambda: generate_coordination_id("msg"))
    schema_version: str = "coordination.v1"
    root_session_id: str = ""
    run_id: str | None = None
    task_id: str | None = None
    sender_session_id: str = ""
    sender_role: str = "general"
    recipient_session_id: str = ""
    recipient_role: str | None = None
    kind: MessageKind = "content"
    intent: str = "task.request"
    payload: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    correlation_id: str | None = None
    in_reply_to: str | None = None
    idempotency_key: str | None = None
    hop_count: int = 0
    max_hops: int = DEFAULT_MAX_HOPS
    ttl_seconds: float | None = None
    message_state: MessageState = "queued"
    consumption_state: ConsumptionState = "unconsumed"
    consumption_receipt: dict[str, Any] | None = None
    effect_unknown_reason: str | None = None
    consumed_at: float | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliveryAttempt:
    """A delivery dispatch record for an AgentMessage."""

    attempt_id: str = field(default_factory=lambda: generate_coordination_id("att"))
    message_id: str = ""
    target_session_id: str = ""
    target_sequence: int | None = None
    target_harness: str | None = None
    delivery_mode: DeliveryMode = "next_turn"
    delivery_state: DeliveryState = "pending"
    injection_receipt: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    attempt_count: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoordinationRun:
    """A multi-agent coordination execution lifecycle."""

    run_id: str = field(default_factory=lambda: generate_coordination_id("run"))
    title: str = "Collaborative Coding Run"
    root_session_id: str = ""
    template: str = "plan_implement_review"
    status: RunStatus = "running"
    budget: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoordinationTask:
    """A distinct task unit in a multi-agent coordination DAG."""

    task_id: str = field(default_factory=lambda: generate_coordination_id("ctask"))
    run_id: str = ""
    title: str = ""
    status: TaskStatus = "queued"
    assignee_session_id: str | None = None
    assignee_role: str | None = None
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    deadline: float | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoordinationEvent:
    """An immutable audit/timeline event emitted during multi-agent coordination."""

    event_id: str = field(default_factory=lambda: generate_coordination_id("cevt"))
    root_session_id: str = ""
    run_id: str | None = None
    task_id: str | None = None
    actor_session_id: str | None = None
    event_type: str = "message.sent"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def category(self) -> str:
        """The normalized event category derived from ``event_type``."""
        return classify_event_category(self.event_type)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category
        return data


@dataclass
class OutboxItem:
    """A durable outbox record waiting for asynchronous capability-aware delivery."""

    item_id: str = field(default_factory=lambda: generate_coordination_id("out"))
    message_id: str = ""
    target_session_id: str = ""
    target_sequence: int | None = None
    status: DeliveryState = "pending"
    payload_json: str = "{}"
    retry_count: int = 0
    next_retry_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutboxReclaim:
    """The outcome of resolving one stale ``leased`` outbox row.

    ``outcome`` is one of ``requeued`` (provably never delivered, handed back
    to the normal retry path), ``unknown`` (delivery may have landed; the row
    is terminal and the message is escalated for reconciliation) or
    ``abandoned`` (retries exhausted, also escalated).
    """

    item_id: str = ""
    message_id: str = ""
    outcome: str = "unknown"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceMergeOperation:
    """A user-confirmed, lease-guarded git merge transaction.

    ``expected_source_head``, ``expected_target_head`` and ``dirty_hash`` are
    captured when the preview is created and re-checked before execution, so
    a stale preview (branches advanced or the worktree changed underneath it)
    can never merge silently.
    """

    operation_id: str = field(default_factory=lambda: generate_coordination_id("mop"))
    root_session_id: str = ""
    holder_session_id: str = ""
    repo_path: str = ""
    source_branch: str = ""
    target_branch: str = "main"
    expected_source_head: str | None = None
    expected_target_head: str | None = None
    dirty_hash: str | None = None
    fencing_token: int | None = None
    status: MergeOperationStatus = "preview"
    preview: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoordinationArtifact:
    """A durable reference to a plan, patch, diff, report or test artifact.

    The blob itself lives in the configured artifact store; this row owns the
    metadata, digest, producer and task/message traceability required by the
    control plane, so messages can reference artifacts without embedding
    full diffs or logs in the envelope.
    """

    artifact_id: str = field(default_factory=lambda: generate_coordination_id("art"))
    root_session_id: str = ""
    run_id: str | None = None
    task_id: str | None = None
    producer_session_id: str = ""
    kind: ArtifactKind = "other"
    digest: str | None = None
    uri: str | None = None
    status: ArtifactStatus = "published"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
