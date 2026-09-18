"""Stable event categories for the control plane's normalized timeline.

The plan's standard event vocabulary is: ``agent | turn | message | tool |
model | provider | workspace | git | artifact | policy | workflow | system``.
Unknown or legacy event types classify honestly as ``other`` rather than
being forced into a category they do not belong to.
"""

from __future__ import annotations

from typing import Literal

EventCategory = Literal[
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
    "other",
]

STANDARD_EVENT_CATEGORIES: tuple[EventCategory, ...] = (
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
)

# Exact matches win over prefixes so ``session.status`` can be ``agent``
# while a future ``session.resource.*`` stays ``tool``.
_EXACT_EVENT_CATEGORIES: dict[str, EventCategory] = {
    "session.status": "agent",
    "session.heartbeat": "system",
    "session.created": "agent",
    "session.updated": "agent",
    "session.deleted": "agent",
    "session.input.consumed": "turn",
    "session.resource.created": "tool",
    "session.resource.deleted": "tool",
    "response.created": "turn",
    "response.in_progress": "turn",
    "response.completed": "turn",
    "response.failed": "turn",
    "response.cancelled": "turn",
    "response.incomplete": "turn",
    "response.output_text.delta": "turn",
    "response.output_item.done": "turn",
    "response.output_reasoning_delta": "turn",
    "external_session_status": "agent",
    "external_session_title": "agent",
    "external_session_todos": "agent",
    "external_session_usage": "model",
    "external_subagent_start": "agent",
    "external_codex_subagent_start": "agent",
    "external_acp_subagent_start": "agent",
    "external_model_change": "model",
    "external_model_options": "model",
    "external_reasoning_effort_change": "model",
    "external_permission_mode_change": "policy",
    "external_codex_approval_mode_change": "policy",
    "external_codex_collaboration_mode_change": "policy",
    "external_conversation_item": "turn",
    "external_output_text_delta": "turn",
    "external_output_reasoning_delta": "turn",
    "external_tool_output_delta": "tool",
    "approval": "policy",
    "interrupt": "turn",
    "compact": "turn",
    "function_call": "tool",
    "tool_call": "tool",
}

# Ordered longest-prefix-first; the first hit wins. ``response.`` is left for
# after the more specific overrides so unlisted SDK response frames still
# classify as turn events.
_EVENT_PREFIX_CATEGORIES: tuple[tuple[str, EventCategory], ...] = (
    ("response.function_call.", "tool"),
    ("agent.", "agent"),
    ("turn.", "turn"),
    ("message.", "message"),
    ("effect.", "message"),
    ("delivery.", "message"),
    ("tool.", "tool"),
    ("mcp.", "tool"),
    ("shell.", "tool"),
    ("model.", "model"),
    ("usage.", "model"),
    ("llm.", "model"),
    ("provider.", "provider"),
    ("gateway.", "provider"),
    ("workspace.", "workspace"),
    ("git.", "git"),
    ("merge.", "git"),
    ("checkout.", "git"),
    ("artifact.", "artifact"),
    ("policy.", "policy"),
    ("permission.", "policy"),
    ("workflow.", "workflow"),
    ("task.", "workflow"),
    ("system.health.", "system"),
    ("health.", "system"),
    ("system.", "system"),
    ("session.resource.", "tool"),
    ("session.", "agent"),
    ("response.", "turn"),
    ("external_", "turn"),
)


def classify_event_category(event_type: str | None) -> EventCategory:
    """Map a durable/session event type to one of the plan's categories.

    Exact known types win, then the longest applicable prefix. ``None`` or an
    unrecognized type returns ``"other"`` so callers can render an honest
    unmatched event instead of hiding it under a plausible category.
    """
    normalized = (event_type or "").strip().lower()
    if not normalized:
        return "other"
    exact = _EXACT_EVENT_CATEGORIES.get(normalized)
    if exact is not None:
        return exact
    for prefix, category in _EVENT_PREFIX_CATEGORIES:
        if normalized.startswith(prefix):
            return category
    return "other"


__all__ = [
    "STANDARD_EVENT_CATEGORIES",
    "EventCategory",
    "classify_event_category",
]
