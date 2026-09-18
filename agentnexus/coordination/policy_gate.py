"""Policy gate for coordination control-plane phases.

Plan §14.2 requires namespaced phases (``coordination_message``,
``workspace_operation``, ``git_merge``) to be evaluated in a fixed order:
ACL/root scope first, then source session policy, target session policy,
run policy, and finally server defaults. DENY short-circuits; ASK is
surfaced to the caller as a fail-closed approval requirement because the
coordination route has no runner-owned approval flow of its own yet.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentnexus.policies.types import EvaluationContext, PolicyResult
from agentnexus.runtime.policies.engine import PolicyEngine
from agentnexus.spec.types import Phase, PolicyAction

COORDINATION_STAGES: tuple[str, ...] = (
    "root",
    "source",
    "target",
    "run",
    "server_default",
)


@dataclass(frozen=True)
class CoordinationPolicyDecision:
    """One composed control-plane policy verdict.

    ``allowed`` is the caller-facing boolean. ``action`` preserves the
    raw engine verdict so route handlers can distinguish ASK (needs
    explicit ACL approval) from DENY (blocked outright).
    """

    allowed: bool
    action: PolicyAction
    stage: str | None = None
    reason: str | None = None
    deciding_policies: tuple[str, ...] | None = None
    required_acl_level: str | None = None


class CoordinationPolicyGate:
    """Evaluate the fixed policy pipeline for one coordination operation.

    The engine factory receives ``(stage, session_id)`` and returns a
    :class:`PolicyEngine` whose policies are scoped to that stage. An
    async factory is required so the production process-local build can
    load agent specs and DB policies off the event loop.
    """

    def __init__(
        self,
        engine_factory: Callable[[str, str], Awaitable[PolicyEngine | None]],
    ) -> None:
        self._engine_factory = engine_factory

    async def evaluate(
        self,
        *,
        phase: Phase,
        root_session_id: str,
        content: object,
        source_session_id: str | None = None,
        target_session_id: str | None = None,
        run_id: str | None = None,
        tool_name: str | None = None,
        actor: dict[str, str] | None = None,
    ) -> CoordinationPolicyDecision:
        """Run the §14.2 pipeline and return the composed verdict.

        Stage order is fixed: root policy, source session, target session,
        run (root owner policy for the coordination run), server default.
        A stage DENY short-circuits immediately. ASKs are accumulated and,
        because this route has no approval side-channel, collapse to a
        fail-closed requirement for ``manage`` ACL approval.
        """
        candidates: list[tuple[str, str]] = [("root", root_session_id)]
        if source_session_id:
            candidates.append(("source", source_session_id))
        if target_session_id:
            candidates.append(("target", target_session_id))
        if run_id:
            candidates.append(("run", root_session_id))
        candidates.append(("server_default", root_session_id))

        asks: list[tuple[str, PolicyResult]] = []
        for stage, session_id in candidates:
            try:
                engine = await self._engine_factory(stage, session_id)
                if engine is None:
                    continue
                result = await engine.evaluate(
                    EvaluationContext(
                        phase=phase,
                        content=content,
                        tool_name=tool_name,
                        actor=actor,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - fail closed on any gate error
                return CoordinationPolicyDecision(
                    allowed=False,
                    action=PolicyAction.DENY,
                    stage=stage,
                    reason=(
                        f"coordination policy gate failed closed at "
                        f"{stage}: {type(exc).__name__}: {exc}"
                    ),
                )
            if result.action == PolicyAction.DENY:
                return CoordinationPolicyDecision(
                    allowed=False,
                    action=PolicyAction.DENY,
                    stage=stage,
                    reason=result.reason or "policy denied coordination operation",
                    deciding_policies=(
                        tuple(result.deciding_policies)
                        if result.deciding_policies
                        else None
                    ),
                )
            if result.action == PolicyAction.ASK:
                asks.append((stage, result))

        if asks:
            reasons = [
                f"{stage}: {result.reason or 'approval required'}"
                for stage, result in asks
            ]
            deciding: list[str] = []
            for _, result in asks:
                if result.deciding_policies:
                    deciding.extend(result.deciding_policies)
            return CoordinationPolicyDecision(
                allowed=False,
                action=PolicyAction.ASK,
                stage=asks[0][0],
                reason="; ".join(reasons),
                deciding_policies=tuple(dict.fromkeys(deciding)) or None,
                required_acl_level="manage",
            )

        return CoordinationPolicyDecision(
            allowed=True,
            action=PolicyAction.ALLOW,
        )


__all__ = [
    "COORDINATION_STAGES",
    "CoordinationPolicyDecision",
    "CoordinationPolicyGate",
]
