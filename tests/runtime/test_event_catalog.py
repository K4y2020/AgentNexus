"""Targeted tests for the control plane's normalized event categories."""

from agentnexus.coordination.types import CoordinationEvent
from agentnexus.event_catalog import (
    STANDARD_EVENT_CATEGORIES,
    classify_event_category,
)


def test_standard_event_catalog_has_all_plan_categories() -> None:
    assert set(STANDARD_EVENT_CATEGORIES) == {
        "agent",
        "turn",
        "message",
        "tool",
        "model",
        "provider",
        "workspace",
        "git",
        "artifact",
        "policy",
        "workflow",
        "system",
    }


def test_classifies_plan_shaped_event_types() -> None:
    cases = {
        "agent.lifecycle.started": "agent",
        "turn.lifecycle.started": "turn",
        "message.delivery.accepted": "message",
        "tool.lifecycle.started": "tool",
        "model.request.started": "model",
        "provider.request.started": "provider",
        "workspace.merged": "workspace",
        "git.preview.created": "git",
        "artifact.published": "artifact",
        "policy.denied": "policy",
        "workflow.started": "workflow",
        "system.health.degraded": "system",
    }
    for event_type, expected in cases.items():
        assert classify_event_category(event_type) == expected


def test_classifies_existing_session_and_coordination_event_types() -> None:
    cases = {
        "session.status": "agent",
        "session.resource.created": "tool",
        "response.failed": "turn",
        "response.function_call.in_progress": "tool",
        "external_session_status": "agent",
        "external_tool_output_delta": "tool",
        "message.rejected": "message",
        "effect.unknown_detected": "message",
        "workflow.task.deadline_exceeded": "workflow",
    }
    for event_type, expected in cases.items():
        assert classify_event_category(event_type) == expected


def test_unknown_event_type_classifies_as_other() -> None:
    assert classify_event_category("vendor_proprietary_blob") == "other"
    assert classify_event_category(None) == "other"
    assert classify_event_category("") == "other"


def test_coordination_event_dict_carries_derived_category() -> None:
    event = CoordinationEvent(event_type="message.delivery.confirmed")
    data = event.to_dict()
    assert data["category"] == "message"
    assert data["event_type"] == "message.delivery.confirmed"

    unknown = CoordinationEvent(event_type="legacy_event")
    assert unknown.to_dict()["category"] == "other"
