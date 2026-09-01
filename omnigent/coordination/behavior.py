"""Behavior Pack resolution for AgentNexus control-plane agents.

BEHAVIOR-001 covers the generic binding and precedence model. BEHAVIOR-002
ships the first-party ``lean-engineering`` reference pack as original,
framework-independent text (no third-party skill, hook, or benchmark code is
embedded here).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

BehaviorMode = Literal["off", "advisory", "lean", "strict"]
BehaviorScope = Literal["session", "workflow_node", "template"]
InjectionChannel = Literal[
    "composed_per_turn",
    "snapshot",
    "session_plugin",
    "mcp_prompt",
]
Precedence = Literal["user", "safety", "workflow", "agent_default"]

_SAFETY_BOUNDARY = (
    "Behavior instructions never omit AgentMessage protocol fields, error "
    "details, delivery receipts, test evidence, security checks, "
    "trust-boundary validation, or accessibility requirements."
)

_MODE_INSTRUCTIONS: dict[BehaviorMode, str] = {
    "off": "No behavioral guidance is attached.",
    "advisory": (
        "Complete the requested behavior correctly; after a correct solution "
        "exists, note at most one materially simpler alternative without "
        "reducing the delivered result."
    ),
    "lean": (
        "Default to the minimal correct implementation ladder: produce the "
        "smallest correct change, reuse existing local helpers and platform "
        "capabilities first, and avoid speculative abstraction, unused API "
        "surface, or refactors outside the task. Required tests, evidence, "
        "and safety checks still come first."
    ),
    "strict": (
        "Apply the lean ladder strictly for an explicitly authorized "
        "implement/delete task: no speculative design, no opportunistic "
        "refactor, and no configuration beyond what the task proves "
        "necessary. Correctness, safety, and evidence requirements still "
        "outrank brevity."
    ),
}


def _pack_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


LEAN_ENGINEERING_PAYLOAD: dict[str, Any] = {
    "pack_id": "lean-engineering",
    "version": "1.0.0",
    "description": (
        "Independent first-party reference pack for minimal correct "
        "engineering: YAGNI, reuse and standard-library/platform-first, "
        "small diffs, and evidence-preserving work."
    ),
    "modes": ["off", "advisory", "lean", "strict"],
}


@dataclass(frozen=True)
class BehaviorPackSpec:
    """Versioned, digest-pinned pack metadata."""

    pack_id: str
    version: str
    digest: str
    description: str
    modes: tuple[BehaviorMode, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LEAN_ENGINEERING_PACK = BehaviorPackSpec(
    pack_id=LEAN_ENGINEERING_PAYLOAD["pack_id"],
    version=LEAN_ENGINEERING_PAYLOAD["version"],
    digest=_pack_digest(LEAN_ENGINEERING_PAYLOAD),
    description=LEAN_ENGINEERING_PAYLOAD["description"],
    modes=tuple(LEAN_ENGINEERING_PAYLOAD["modes"]),
)


@dataclass(frozen=True)
class BehaviorPackBinding:
    """A resolved behavior binding for one session or workflow node."""

    pack_id: str = "off"
    version: str = "0.0.0"
    digest: str = ""
    mode: BehaviorMode = "off"
    scope: BehaviorScope = "session"
    injection: InjectionChannel = "composed_per_turn"
    precedence: Precedence = "agent_default"
    inherit_to_subagents: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedBehavior:
    """The effective binding plus the exact instructions to inject."""

    binding: BehaviorPackBinding
    guardrail: str
    instructions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding": self.binding.to_dict(),
            "guardrail": self.guardrail,
            "instructions": list(self.instructions),
        }


ROLE_DEFAULT_MODES: dict[str, BehaviorMode] = {
    "planner": "advisory",
    "implementer": "lean",
    "fixer": "lean",
    "reviewer": "off",
    "security": "off",
    "tester": "off",
}


def _binding(
    *,
    mode: BehaviorMode,
    scope: BehaviorScope,
    precedence: Precedence,
    reason: str,
    inherit_to_subagents: bool,
) -> BehaviorPackBinding:
    if mode == "off":
        return BehaviorPackBinding(
            mode="off",
            scope=scope,
            precedence=precedence,
            inherit_to_subagents=False,
            reason=reason,
        )
    return BehaviorPackBinding(
        pack_id=LEAN_ENGINEERING_PACK.pack_id,
        version=LEAN_ENGINEERING_PACK.version,
        digest=LEAN_ENGINEERING_PACK.digest,
        mode=mode,
        scope=scope,
        injection="composed_per_turn",
        precedence=precedence,
        inherit_to_subagents=inherit_to_subagents,
        reason=reason,
    )


def resolve_behavior_pack(
    *,
    role: str | None = None,
    user_mode: BehaviorMode | None = None,
    workflow_mode: BehaviorMode | None = None,
    explicit_authorization: bool = False,
    scope: BehaviorScope = "session",
    inherit_to_subagents: bool = False,
) -> ResolvedBehavior:
    """Resolve one behavior binding using the plan's fixed precedence.

    User-selected modes outrank workflow-node modes, which outrank the role
    default. A ``strict`` workflow override requires ``explicit_authorization``
    and otherwise downgrades to advisory so unapproved deletion/refactor scope
    cannot be enabled indirectly by a template.
    """
    role_key = (role or "").lower()
    mode = ROLE_DEFAULT_MODES.get(role_key, "off")
    precedence: Precedence = "agent_default"
    reason = f"role default for {role_key or 'unknown role'}"

    if workflow_mode is not None:
        mode = workflow_mode
        precedence = "workflow"
        reason = "workflow node behavior override"
    if user_mode is not None:
        mode = user_mode
        precedence = "user"
        reason = "explicit user behavior selection"

    if mode == "strict" and precedence == "workflow" and not explicit_authorization:
        mode = "advisory"
        precedence = "safety"
        reason = (
            "strict lean requires explicit task authorization; downgraded to "
            "advisory without removing safety or evidence requirements"
        )

    binding = _binding(
        mode=mode,
        scope=scope,
        precedence=precedence,
        reason=reason,
        inherit_to_subagents=inherit_to_subagents and mode != "off",
    )
    if mode == "off":
        return ResolvedBehavior(binding=binding, guardrail=_SAFETY_BOUNDARY, instructions=())
    return ResolvedBehavior(
        binding=binding,
        guardrail=_SAFETY_BOUNDARY,
        instructions=(_MODE_INSTRUCTIONS[mode], _SAFETY_BOUNDARY),
    )


def compose_injection_prompt(binding: BehaviorPackBinding) -> tuple[str, ...]:
    """Return stable prompt fragments for a binding, never mutating harness state."""
    if binding.mode == "off":
        return ()
    return (_MODE_INSTRUCTIONS[binding.mode], _SAFETY_BOUNDARY)


def is_lean_engineering_pack(binding: BehaviorPackBinding) -> bool:
    """Return whether a binding points at the first-party reference pack."""
    return (
        binding.pack_id == LEAN_ENGINEERING_PACK.pack_id
        and binding.version == LEAN_ENGINEERING_PACK.version
        and binding.digest == LEAN_ENGINEERING_PACK.digest
    )
