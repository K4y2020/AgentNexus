"""Read-only diagnostics routes for the transparent cockpit.

``GET /v1/diagnostics/health`` builds the layered health topology from
authoritative server state: the running server, registered hosts, runner
bindings from conversations, harness readiness reported by hosts, and the
durable last-task-error labels on those conversations. Layers the server
cannot probe (UI and provider/tool liveness on this replica) are marked
``unknown`` rather than guessed.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from agentnexus.error_layers import ERROR_LAYERS, ErrorLayer, classify_error_layer
from agentnexus.runner.routing import RunnerRouter
from agentnexus.server.auth import RESERVED_USER_LOCAL, AuthProvider
from agentnexus.server.routes._auth_helpers import require_user
from agentnexus.server.routes._sessions.common import (
    _LAST_TASK_ERROR_CODE_LABEL_KEY,
    _LAST_TASK_ERROR_LAYER_LABEL_KEY,
    _LAST_TASK_ERROR_MESSAGE_LABEL_KEY,
)
from agentnexus.stores import ConversationStore
from agentnexus.stores.host_store import Host, HostStore, host_is_live
from agentnexus.version import VERSION

_PROCESS_STARTED_AT = time.time()
_SESSION_SCAN_LIMIT = 1000
_SESSION_PAGE_SIZE = 200

DiagnosticsStatus = Literal["healthy", "degraded", "unhealthy", "unknown"]


class HealthNode(BaseModel):
    """One layer of the control-plane health topology."""

    layer: ErrorLayer
    name: str
    status: DiagnosticsStatus
    detail: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    last_error: dict[str, str] | None = None


class HealthEdge(BaseModel):
    """Directed dependency edge between two health layers."""

    source: str
    target: str
    label: str | None = None


class ErrorCluster(BaseModel):
    """Recent durable failure clustered by one of the 11 error layers."""

    layer: ErrorLayer
    count: int
    codes: list[str] = Field(default_factory=list)
    last_error: dict[str, str] | None = None


class HealthTopology(BaseModel):
    """Snapshot of the layered control-plane health graph."""

    generated_at: float
    status: DiagnosticsStatus
    nodes: list[HealthNode] = Field(default_factory=list)
    edges: list[HealthEdge] = Field(default_factory=list)
    error_clusters: list[ErrorCluster] = Field(default_factory=list)
    scanned_sessions: int = 0
    scan_truncated: bool = False


def _layer_status(error_count: int) -> DiagnosticsStatus:
    """Map a durable-error count to a health status for an observable layer."""
    return "unhealthy" if error_count > 0 else "unknown"


def _last_error_for_layer(
    last_errors: dict[ErrorLayer, dict[str, str]],
    layer: ErrorLayer,
) -> dict[str, str] | None:
    return last_errors.get(layer)


def _build_health_topology(
    *,
    conversation_store: ConversationStore,
    user_id: str | None,
    host_store: HostStore | None = None,
    runner_router: RunnerRouter | None = None,
) -> HealthTopology:
    """Collect one honest health snapshot from server-side sources.

    Host rows come from :meth:`HostStore.list_hosts` and runner bindings from
    the caller's accessible conversations, so the graph is scoped to the
    requesting user instead of leaking another user's hosts/runners.
    """
    now = time.time()
    errors: dict[ErrorLayer, int] = {}
    last_errors: dict[ErrorLayer, dict[str, str]] = {}
    runner_ids: dict[str, list[str]] = {}
    scanned = 0
    truncated = False
    after: str | None = None

    while True:
        page = conversation_store.list_conversations(
            limit=_SESSION_PAGE_SIZE,
            after=after,
            kind=None,
            accessible_by=user_id,
            include_archived=False,
        )
        if not page.data:
            break
        for conv in page.data:
            if scanned >= _SESSION_SCAN_LIMIT:
                truncated = True
                break
            scanned += 1
            if conv.runner_id:
                runner_ids.setdefault(conv.runner_id, []).append(conv.id)
            raw_layer = conv.labels.get(_LAST_TASK_ERROR_LAYER_LABEL_KEY)
            raw_code = conv.labels.get(_LAST_TASK_ERROR_CODE_LABEL_KEY)
            layer = raw_layer if raw_layer in ERROR_LAYERS else None
            if layer is None and raw_code:
                layer = classify_error_layer(raw_code)
            if layer in ERROR_LAYERS:
                errors[layer] = errors.get(layer, 0) + 1
                last_errors[layer] = {
                    "layer": layer,
                    "code": raw_code or "",
                    "message": conv.labels.get(_LAST_TASK_ERROR_MESSAGE_LABEL_KEY, ""),
                }
        if truncated or not page.has_more:
            break
        after = page.last_id

    # ── Server ────────────────────────────────────────────────
    server_node = HealthNode(
        layer="server",
        name="Server",
        status="healthy",
        detail=f"Control plane {VERSION} is serving requests.",
        metrics={
            "version": VERSION,
            "pid": os.getpid(),
            "uptime_s": round(now - _PROCESS_STARTED_AT, 1),
            "sessions_scanned": scanned,
        },
    )

    # ── UI ────────────────────────────────────────────────────
    ui_node = HealthNode(
        layer="ui",
        name="Web UI",
        status="unknown",
        detail="Client-side layer; the server cannot prove a live browser session.",
        metrics={"connected_clients": 0},
    )

    # ── Hosts ─────────────────────────────────────────────────
    host_metrics: dict[str, int] = {"known": 0, "online": 0, "offline": 0}
    host_issues: list[str] = []
    host_status: DiagnosticsStatus = "unknown"
    host_detail = "Host store not wired on this server."
    if host_store is not None:
        hosts = host_store.list_hosts(user_id or RESERVED_USER_LOCAL)
        host_metrics["known"] = len(hosts)
        online_hosts: list[Host] = []
        offline_hosts: list[Host] = []
        for host in hosts:
            if host_is_live(host):
                online_hosts.append(host)
            else:
                offline_hosts.append(host)
        host_metrics["online"] = len(online_hosts)
        host_metrics["offline"] = len(offline_hosts)
        host_issues = [f"host {h.name or h.host_id} is offline/stale" for h in offline_hosts]
        if not hosts:
            host_status = "healthy"
            host_detail = "No hosts registered for this user."
        elif offline_hosts and not online_hosts:
            host_status = "unhealthy"
            host_detail = f"{len(offline_hosts)} of {len(hosts)} hosts are unavailable."
        elif offline_hosts:
            host_status = "degraded"
            host_detail = f"{len(online_hosts)} online, {len(offline_hosts)} unavailable."
        else:
            host_status = "healthy"
            host_detail = f"{len(online_hosts)} hosts online."
    host_node = HealthNode(
        layer="host",
        name="Host",
        status=host_status,
        detail=host_detail,
        metrics=host_metrics,
        issues=host_issues,
    )

    # ── Runners ───────────────────────────────────────────────
    runner_metrics: dict[str, int] = {
        "bound": len(runner_ids),
        "online": 0,
        "offline": 0,
        "bound_sessions": sum(len(ids) for ids in runner_ids.values()),
    }
    runner_issues: list[str] = []
    if not runner_ids:
        runner_status: DiagnosticsStatus = "healthy"
        runner_detail = "No runner-bound sessions for this user."
    elif runner_router is None:
        runner_status = "unknown"
        runner_detail = "Runner router not wired on this server replica."
    else:
        for runner_id, _sessions in runner_ids.items():
            if runner_router.runner_is_online(runner_id):
                runner_metrics["online"] += 1
            else:
                runner_metrics["offline"] += 1
                runner_issues.append(f"runner {runner_id} is offline")
        if runner_metrics["offline"] and not runner_metrics["online"]:
            runner_status = "unhealthy"
            runner_detail = "All bound runners are offline."
        elif runner_metrics["offline"]:
            runner_status = "degraded"
            runner_detail = "Some bound runners are offline."
        else:
            runner_status = "healthy"
            runner_detail = "All bound runners are online."
    runner_node = HealthNode(
        layer="runner",
        name="Runner",
        status=runner_status,
        detail=runner_detail,
        metrics=runner_metrics,
        issues=runner_issues,
    )

    # ── Harnesses ─────────────────────────────────────────────
    harness_metrics: dict[str, int] = {"ready": 0, "not_ready": 0, "reported": 0}
    harness_issues: list[str] = []
    if host_store is not None:
        for host in host_store.list_hosts(user_id or RESERVED_USER_LOCAL):
            if not host_is_live(host):
                continue
            for harness, availability in (host.configured_harnesses or {}).items():
                harness_metrics["reported"] += 1
                if availability is True:
                    harness_metrics["ready"] += 1
                else:
                    harness_metrics["not_ready"] += 1
                    harness_issues.append(
                        f"{harness} on {host.name or host.host_id}: {availability}"
                    )
    harness_errors = errors.get("harness", 0)
    if harness_errors:
        harness_status: DiagnosticsStatus = "unhealthy"
        harness_detail = "Recent harness failures were recorded."
    elif harness_issues:
        harness_status = "degraded"
        harness_detail = "Some harnesses reported not-ready on a live host."
    elif harness_metrics["reported"]:
        harness_status = "healthy"
        harness_detail = "All reported harnesses are ready."
    else:
        harness_status = "unknown"
        harness_detail = "No live host reported harness readiness."
    harness_node = HealthNode(
        layer="harness",
        name="Harness",
        status=harness_status,
        detail=harness_detail,
        metrics=harness_metrics,
        issues=harness_issues,
        last_error=_last_error_for_layer(last_errors, "harness"),
    )

    # ── Provider / Tool / workspace / git / policy ────────────
    provider_node = HealthNode(
        layer="provider",
        name="Provider",
        status=_layer_status(errors.get("provider", 0)),
        detail=(
            "Recent provider failures were recorded."
            if errors.get("provider")
            else "No gateway probe on this replica; provider health comes from session errors."
        ),
        metrics={"error_count": errors.get("provider", 0)},
        last_error=_last_error_for_layer(last_errors, "provider"),
    )
    tool_node = HealthNode(
        layer="tool",
        name="Tool",
        status=_layer_status(errors.get("tool", 0)),
        detail=(
            "Recent tool failures were recorded."
            if errors.get("tool")
            else "No tool probe on this replica; tool health comes from session errors."
        ),
        metrics={"error_count": errors.get("tool", 0)},
        last_error=_last_error_for_layer(last_errors, "tool"),
    )
    workspace_node = HealthNode(
        layer="workspace",
        name="Workspace",
        status=_layer_status(errors.get("workspace", 0)),
        detail=(
            "Recent workspace failures were recorded."
            if errors.get("workspace")
            else "No workspace errors recorded in the scanned sessions."
        ),
        metrics={"error_count": errors.get("workspace", 0)},
        last_error=_last_error_for_layer(last_errors, "workspace"),
    )
    git_node = HealthNode(
        layer="git",
        name="Git",
        status=_layer_status(errors.get("git", 0)),
        detail=(
            "Recent git failures were recorded."
            if errors.get("git")
            else "No git errors recorded in the scanned sessions."
        ),
        metrics={"error_count": errors.get("git", 0)},
        last_error=_last_error_for_layer(last_errors, "git"),
    )
    policy_node = HealthNode(
        layer="policy",
        name="Policy",
        status=_layer_status(errors.get("policy", 0)),
        detail=(
            "Recent policy denials were recorded."
            if errors.get("policy")
            else "No policy errors recorded in the scanned sessions."
        ),
        metrics={"error_count": errors.get("policy", 0)},
        last_error=_last_error_for_layer(last_errors, "policy"),
    )

    nodes = [
        ui_node,
        server_node,
        host_node,
        runner_node,
        harness_node,
        provider_node,
        tool_node,
        workspace_node,
        git_node,
        policy_node,
    ]
    edges = [
        HealthEdge(source="ui", target="server"),
        HealthEdge(source="server", target="host"),
        HealthEdge(source="server", target="runner"),
        HealthEdge(source="host", target="runner"),
        HealthEdge(source="runner", target="harness"),
        HealthEdge(source="harness", target="provider"),
        HealthEdge(source="harness", target="tool"),
    ]
    clusters = [
        ErrorCluster(
            layer=layer,
            count=count,
            codes=sorted(
                {last_errors[layer]["code"] for layer in (layer,) if layer in last_errors}
            ),
            last_error=last_errors.get(layer),
        )
        for layer, count in sorted(errors.items(), key=lambda item: (-item[1], item[0]))
    ]

    statuses = [node.status for node in nodes]
    overall: DiagnosticsStatus = (
        "unhealthy"
        if "unhealthy" in statuses
        else "degraded"
        if "degraded" in statuses
        else "healthy"
    )
    return HealthTopology(
        generated_at=now,
        status=overall,
        nodes=nodes,
        edges=edges,
        error_clusters=clusters,
        scanned_sessions=scanned,
        scan_truncated=truncated,
    )


def create_diagnostics_router(
    conversation_store: ConversationStore,
    *,
    auth_provider: AuthProvider | None = None,
    host_store: HostStore | None = None,
    runner_router: RunnerRouter | None = None,
) -> APIRouter:
    """Create the read-only diagnostics router mounted under ``/v1``."""
    router = APIRouter()

    @router.get("/diagnostics/health", response_model=HealthTopology)
    async def get_health_topology(request: Request) -> HealthTopology:
        """Return the user-scoped layered health snapshot."""
        user_id = require_user(request, auth_provider)
        return await asyncio.to_thread(
            _build_health_topology,
            conversation_store=conversation_store,
            user_id=user_id,
            host_store=host_store,
            runner_router=runner_router,
        )

    return router


__all__ = [
    "ErrorCluster",
    "HealthEdge",
    "HealthNode",
    "HealthTopology",
    "create_diagnostics_router",
]
