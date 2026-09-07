"""Correlated A2A completion; a terminal turn alone is not a task result."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from omnigent.coordination.channels import (
    A2A_CHANNEL_KIND_KEY,
    A2A_CHANNEL_SCOPE_KEY,
    A2A_CHANNEL_SOURCE_KEY,
)
from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.types import AgentMessage

if TYPE_CHECKING:
    from omnigent.stores import ConversationStore


def result_message(request: AgentMessage, text: str, outcome: str = "succeeded") -> AgentMessage:
    return AgentMessage(
        root_session_id=request.root_session_id,
        run_id=request.run_id,
        task_id=request.task_id,
        sender_session_id=request.recipient_session_id,
        sender_role=request.recipient_role or "teammate",
        recipient_session_id=request.sender_session_id,
        recipient_role=request.sender_role,
        kind="event",
        intent="task.result",
        payload={
            "prompt": text,
            "summary": text,
            "outcome": outcome,
            "source_bot_id": request.payload.get("target_bot_id"),
            "target_bot_id": request.payload.get("source_bot_id"),
            **{
                key: request.payload[key]
                for key in (A2A_CHANNEL_SCOPE_KEY, A2A_CHANNEL_KIND_KEY, A2A_CHANNEL_SOURCE_KEY)
                if key in request.payload
            },
        },
        correlation_id=request.correlation_id or request.message_id,
        in_reply_to=request.message_id,
        idempotency_key=f"a2a:result:{request.message_id}",
        # Terminal replies cannot spawn another request and don't consume a hop.
        hop_count=request.hop_count,
        max_hops=request.max_hops,
    )


def record_declared_results(
    store: CoordinationStore,
    session_id: str,
    text: str,
) -> int:
    """Persist only a final response explicitly naming its durable request."""
    marker = re.search(r"\[A2A_RESULT:(msg_[a-zA-Z0-9]+):(succeeded|failed)\]\s*$", text)
    if marker is None:
        return 0
    request = store.get_message(marker.group(1))
    if (
        request is None
        or request.kind != "command"
        or request.run_id is not None
        or request.intent == "task.result"
        or request.recipient_session_id != session_id
        or request.message_state in ("cancelled", "expired")
    ):
        return 0
    report = text[: marker.start()].strip()
    if not report:
        return 0
    store.save_message_and_outbox(result_message(request, report, marker.group(2)))
    return 1


def recover_declared_result(
    store: CoordinationStore,
    conversations: ConversationStore,
    request: AgentMessage,
) -> None:
    """Recover a missed terminal hook from persisted assistant declarations."""
    after = None
    while True:
        page = conversations.list_items(
            request.recipient_session_id, order="desc", limit=50, after=after, type="message"
        )
        for item in page.data:
            # Transcript timestamps may have only whole-second precision.
            if item.created_at < int(request.created_at):
                return
            if item.status != "completed" or getattr(item.data, "role", None) != "assistant":
                continue
            text = "".join(
                block.get("text", "")
                for block in (getattr(item.data, "content", None) or [])
                if isinstance(block, dict) and block.get("type") in ("output_text", "text")
            )
            if f"[A2A_RESULT:{request.message_id}:" in text:
                record_declared_results(store, request.recipient_session_id, text)
                return
        if not page.has_more or not page.data:
            return
        after = page.data[-1].id
