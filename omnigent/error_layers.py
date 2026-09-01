"""Stable 11-layer error classification shared by runner, server and UI.

The plan's unified error envelope classifies every failure into one of:
``ui | server | host | runner | harness | model | provider | tool |
workspace | git | policy``.  Layer strings stay literal so existing
``code``/``source`` values can be enriched without changing client code.
"""

from typing import Literal

ErrorLayer = Literal[
    "ui",
    "server",
    "host",
    "runner",
    "harness",
    "model",
    "provider",
    "tool",
    "workspace",
    "git",
    "policy",
]

ERROR_LAYERS: tuple[ErrorLayer, ...] = (
    "ui",
    "server",
    "host",
    "runner",
    "harness",
    "model",
    "provider",
    "tool",
    "workspace",
    "git",
    "policy",
)

_EXACT_CODE_LAYERS: dict[str, ErrorLayer] = {
    "runner_error": "runner",
    "runner_disconnected": "runner",
    "runner_failed_to_start": "runner",
    "runner_unavailable": "runner",
    "runner_rejected_event": "runner",
    "runner_offline": "runner",
    "runner_turn_context_desync": "runner",
    "harness_not_configured": "harness",
    "terminal_launch_failed": "harness",
    "required_terminal_exited": "harness",
    "native_terminal_start_failed": "harness",
    "native_terminal_ensure_failed": "harness",
    "model_change_not_applied": "harness",
    "workspace_missing": "workspace",
    "policy_denied": "policy",
    "policy_disabled": "policy",
    "policy_not_enforced": "policy",
    "rate_limit_exceeded": "model",
    "context_length_exceeded": "model",
    "timeout": "model",
    "llm_auth_failed": "model",
    "provider_rate_limited": "provider",
    "gateway_unavailable": "provider",
    "connection_error": "provider",
    "server_error": "provider",
    "invalid_input": "server",
    "internal_error": "server",
    "session_not_found": "server",
    "wrong_replica": "server",
    "forbidden": "server",
    "unauthorized": "server",
    "host_offline": "host",
    "host_not_found": "host",
    "host_not_owned": "host",
    "host_registry_unavailable": "host",
}

_PREFIX_CODE_LAYERS: tuple[tuple[str, ErrorLayer], ...] = (
    ("runner_", "runner"),
    ("harness_", "harness"),
    ("terminal_", "harness"),
    ("native_", "harness"),
    ("codex_", "harness"),
    ("claude_", "harness"),
    ("opencode_", "harness"),
    ("cursor_", "harness"),
    ("qwen_", "harness"),
    ("antigravity_", "harness"),
    ("host_", "host"),
    ("policy_", "policy"),
    ("permission_", "policy"),
    ("workspace_", "workspace"),
    ("worktree_", "workspace"),
    ("git_", "git"),
    ("merge_", "git"),
    ("checkout_", "git"),
    ("provider_", "provider"),
    ("gateway_", "provider"),
    ("upstream_", "provider"),
    ("llm_", "model"),
    ("model_", "model"),
    ("tool_", "tool"),
    ("mcp_", "tool"),
    ("shell_", "tool"),
    ("os_", "tool"),
    ("session_", "server"),
    ("server_", "server"),
    ("database_", "server"),
    ("migration_", "server"),
    ("ui_", "ui"),
)

_SOURCE_LAYERS: dict[str, ErrorLayer] = {
    "llm": "model",
    "tool": "tool",
    "harness": "harness",
    "host": "host",
    "execution": "runner",
}


def classify_error_layer(
    code: str | None,
    *,
    source: str | None = None,
    fallback: ErrorLayer = "server",
) -> ErrorLayer:
    """Map a stable error code (and optional source) to one of the 11 layers.

    Exact code matches win, then the longest applicable prefix, then the
    known ``source`` label (``llm``/``tool``/``harness``/``host``/
    ``execution``), then the caller's fallback.  Unknown codes never leave
    the caller without a layer.
    """
    normalized = (code or "").strip().lower()
    if normalized:
        exact = _EXACT_CODE_LAYERS.get(normalized)
        if exact is not None:
            return exact
        for prefix, layer in _PREFIX_CODE_LAYERS:
            if normalized.startswith(prefix):
                return layer
    if source:
        return _SOURCE_LAYERS.get(source.strip().lower(), fallback)
    return fallback
