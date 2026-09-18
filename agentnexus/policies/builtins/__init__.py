"""Built-in policy functions shipped with AgentNexus.

Each submodule exports a ``POLICY_REGISTRY`` list — a catalog of
policy callables with their handler paths, descriptions, and
parameter schemas. The server discovers these at startup and
exposes them via ``GET /v1/policy-registry`` so users can browse
available policies and attach them to sessions.

The ``POLICY_REGISTRY`` convention::

    POLICY_REGISTRY = [
        {
            "handler": "agentnexus.policies.builtins.safety.max_tool_calls_per_session",
            "kind": "factory",  # called with factory_params to produce evaluator
            "description": "Limits tool calls per session",
            "params_schema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max calls allowed per turn",
                        "default": 10,
                    }
                },
                "required": ["limit"],
            },
        },
    ]

Modules to scan are listed in :data:`BUILTIN_POLICY_MODULES`.
"""

from __future__ import annotations

# Modules scanned at startup for POLICY_REGISTRY entries.
# Add new builtin modules here.
BUILTIN_POLICY_MODULES = [
    "agentnexus.policies.builtins.safety",
    "agentnexus.policies.builtins.cost",
    "agentnexus.policies.builtins.google",
    "agentnexus.policies.builtins.github",
    "agentnexus.policies.builtins.working_dir",
    "agentnexus.policies.builtins.risk_score",
    "agentnexus.policies.builtins.routing",
    "agentnexus.policies.builtins.cel",
    "agentnexus.policies.builtins.prompt",
    "agentnexus.policies.builtins.context",
    "agentnexus.policies.builtins.orchestration",
    # Legacy alias module — registers old omnigent.inner.nessie.policies.*
    # handler paths so deployed bundles that pre-date the rename still work.
    "agentnexus.inner.nessie.policies",
]
