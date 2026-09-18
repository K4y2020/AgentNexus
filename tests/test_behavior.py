"""Tests for Behavior Pack precedence, lean-engineering, and safety bounds."""

from __future__ import annotations

from agentnexus.coordination.behavior import (
    LEAN_ENGINEERING_PACK,
    compose_injection_prompt,
    framework_instructions_for_session,
    is_lean_engineering_pack,
    resolve_behavior_pack,
    session_behavior_mode_from_labels,
    workflow_behavior_payload,
)


def test_lean_engineering_pack_is_digest_pinned() -> None:
    assert LEAN_ENGINEERING_PACK.pack_id == "lean-engineering"
    assert LEAN_ENGINEERING_PACK.version == "1.0.0"
    assert LEAN_ENGINEERING_PACK.digest.startswith("sha256:")
    assert set(LEAN_ENGINEERING_PACK.modes) == {"off", "advisory", "lean", "strict"}

    first = resolve_behavior_pack(role="implementer").binding.digest
    second = resolve_behavior_pack(role="implementer").binding.digest
    assert first == second == LEAN_ENGINEERING_PACK.digest


def test_role_defaults_map_implementer_and_reviewer_correctly() -> None:
    implementer = resolve_behavior_pack(role="implementer")
    planner = resolve_behavior_pack(role="planner")
    reviewer = resolve_behavior_pack(role="reviewer")

    assert implementer.binding.mode == "lean"
    assert planner.binding.mode == "advisory"
    assert reviewer.binding.mode == "off"
    assert reviewer.binding.precedence == "agent_default"


def test_user_selection_outranks_workflow_and_role_default() -> None:
    workflow = resolve_behavior_pack(
        role="planner",
        workflow_mode="lean",
    )
    user = resolve_behavior_pack(
        role="planner",
        workflow_mode="lean",
        user_mode="advisory",
    )

    assert workflow.binding.mode == "lean"
    assert workflow.binding.precedence == "workflow"
    assert user.binding.mode == "advisory"
    assert user.binding.precedence == "user"


def test_strict_workflow_requires_explicit_authorization() -> None:
    unapproved = resolve_behavior_pack(
        role="implementer",
        workflow_mode="strict",
        explicit_authorization=False,
    )
    approved = resolve_behavior_pack(
        role="implementer",
        workflow_mode="strict",
        explicit_authorization=True,
    )

    assert unapproved.binding.mode == "advisory"
    assert unapproved.binding.precedence == "safety"
    assert "explicit task authorization" in unapproved.binding.reason
    assert approved.binding.mode == "strict"
    assert approved.binding.precedence == "workflow"


def test_injection_prompt_never_skips_guardrail() -> None:
    off = resolve_behavior_pack(role="reviewer")
    lean = resolve_behavior_pack(role="implementer")

    assert compose_injection_prompt(off.binding) == ()
    assert off.binding.inherit_to_subagents is False
    fragments = compose_injection_prompt(lean.binding)
    assert len(fragments) == 2
    assert "smallest correct change" in fragments[0]
    assert "security checks" in fragments[1]
    assert is_lean_engineering_pack(lean.binding)


def test_framework_instructions_for_session_label() -> None:
    assert framework_instructions_for_session(None) == ()
    assert framework_instructions_for_session({}) == ()
    assert framework_instructions_for_session({"agentnexus.behavior_mode": "bogus"}) == ()
    assert framework_instructions_for_session({"agentnexus.behavior_mode": "off"}) == ()

    fragments = framework_instructions_for_session({"agentnexus.behavior_mode": "lean"})
    assert len(fragments) == 2
    assert "smallest correct change" in fragments[0]
    assert "security checks" in fragments[1]

    advisory = framework_instructions_for_session({"agentnexus.behavior_mode": "advisory"})
    assert len(advisory) == 2
    assert advisory[0] != fragments[0]


def test_resolved_behavior_serializes_without_list_as_tuple_leak() -> None:
    resolved = resolve_behavior_pack(role="implementer")
    payload = resolved.to_dict()

    assert isinstance(payload["instructions"], list)
    assert payload["binding"]["mode"] == "lean"
    assert payload["binding"]["digest"] == LEAN_ENGINEERING_PACK.digest


def test_workflow_behavior_payload_records_requested_and_downgrade() -> None:
    payload = workflow_behavior_payload(
        role="implementer",
        workflow_mode="strict",
    )

    assert payload["workflow_node"] == "implementer"
    assert payload["requested_mode"] == "strict"
    assert payload["injection_channel"] == "composed_per_turn"
    resolved = payload["resolved"]
    assert resolved["binding"]["mode"] == "advisory"
    assert "explicit task authorization" in resolved["binding"]["reason"]


def test_session_behavior_mode_reads_only_valid_label() -> None:
    assert session_behavior_mode_from_labels({"agentnexus.behavior_mode": "lean"}) == "lean"
    assert session_behavior_mode_from_labels({"agentnexus.behavior_mode": "strict"}) == "strict"
    assert session_behavior_mode_from_labels({"agentnexus.behavior_mode": "bogus"}) is None
    assert session_behavior_mode_from_labels({}) is None
    assert session_behavior_mode_from_labels(None) is None
