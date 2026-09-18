"""Small, durable channel-binding helpers for A2A delivery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

A2A_CHANNEL_LABEL = "agentnexus.teammate.channel"
A2A_CHANNEL_SCOPE_LABEL = "agentnexus.teammate.channel_scope"
A2A_CHANNEL_KIND_LABEL = "agentnexus.teammate.channel_kind"
A2A_CHANNEL_SOURCE_LABEL = "agentnexus.teammate.channel_source"

A2A_CHANNEL_SCOPE_KEY = "a2a_channel_scope"
A2A_CHANNEL_KIND_KEY = "a2a_channel_kind"
A2A_CHANNEL_SOURCE_KEY = "a2a_channel_source_session_id"


@dataclass(frozen=True)
class A2AChannelBinding:
    """The source context a dedicated teammate channel belongs to."""

    kind: str
    source_session_id: str
    scope: str


def binding_for_session(
    session_id: str,
    *,
    purpose: str | None = None,
    root_session_id: str | None = None,
    labels: Mapping[str, object] | None = None,
) -> A2AChannelBinding:
    """Derive a stable Chat/Topic scope from a session snapshot or entity."""
    labels = labels or {}
    saved_scope = labels.get(A2A_CHANNEL_SCOPE_LABEL)
    if purpose == "a2a" and isinstance(saved_scope, str) and saved_scope:
        saved_kind = labels.get(A2A_CHANNEL_KIND_LABEL)
        kind = saved_kind if saved_kind in {"chat", "topic"} else "chat"
        source = labels.get(A2A_CHANNEL_SOURCE_LABEL)
        return A2AChannelBinding(
            kind=kind,
            source_session_id=source if isinstance(source, str) and source else session_id,
            scope=saved_scope,
        )

    source_session_id = root_session_id or session_id
    kind = "topic" if purpose == "topic" else "chat"
    return A2AChannelBinding(
        kind=kind,
        source_session_id=source_session_id,
        scope=f"{kind}:{source_session_id}",
    )


def labels_for_binding(binding: A2AChannelBinding) -> dict[str, str]:
    """Return the labels stamped on a scoped A2A session."""
    return {
        A2A_CHANNEL_LABEL: "a2a",
        A2A_CHANNEL_SCOPE_LABEL: binding.scope,
        A2A_CHANNEL_KIND_LABEL: binding.kind,
        A2A_CHANNEL_SOURCE_LABEL: binding.source_session_id,
    }


def payload_for_binding(binding: A2AChannelBinding) -> dict[str, str]:
    """Return the message metadata that keeps replies on the same channel."""
    return {
        A2A_CHANNEL_SCOPE_KEY: binding.scope,
        A2A_CHANNEL_KIND_KEY: binding.kind,
        A2A_CHANNEL_SOURCE_KEY: binding.source_session_id,
    }
