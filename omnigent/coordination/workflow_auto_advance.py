"""Best-effort automatic advancement of fixed workflow stages.

The durable outbox proves a request was injected, and the terminal-idle
receipt proves a turn ended, but neither proves the agent produced the
controlled stage outcome. This bridge only advances a workflow when the
recipient's latest assistant message declares exactly the marker the stage
prompt asked for; anything else stays on the manual report/retry path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from omnigent.coordination.types import AgentMessage
from omnigent.coordination.workflow_engine import CoordinationWorkflowEngine
from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)

_RESULT_RE = re.compile(
    r"\[WORKFLOW_RESULT\s*:\s*(succeeded|failed)\s*\]",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\[REVIEW_DECISION\s*:\s*(approved|changes_requested)\s*\]",
    re.IGNORECASE,
)


def assistant_text_from_items(items: Any, response_id: str | None = None) -> str:
    """Return the newest matching assistant message text from a paged item list."""
    rows = list(getattr(items, "data", items) or [])
    if response_id:
        scoped = [item for item in rows if getattr(item, "response_id", None) == response_id]
        if scoped:
            rows = scoped
    for item in rows:
        data = getattr(item, "data", None)
        if getattr(data, "role", None) != "assistant":
            continue
        text = _text_from_content(getattr(data, "content", None))
        if text:
            return text
    return ""


def _text_from_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in (None, "output_text", "text"):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return " ".join(parts).strip()


def declared_outcome(
    text: str,
    role: str | None,
    intent: str | None,
) -> tuple[str | None, str | None]:
    """Extract a controlled stage outcome and optional review decision."""
    result = _RESULT_RE.search(text or "")
    decision = _DECISION_RE.search(text or "")
    review_decision = decision.group(1).lower() if decision else None
    outcome = result.group(1).lower() if result else None
    if outcome:
        return outcome, review_decision
    if (
        role == "reviewer"
        and intent is not None
        and "review" in intent
        and review_decision is not None
    ):
        return "succeeded", review_decision
    return None, None


class WorkflowAutoAdvancer:
    """Turn a declared terminal assistant result into one durable workflow advance."""

    def __init__(
        self,
        engine: CoordinationWorkflowEngine,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        self.engine = engine
        self.conversation_store = conversation_store

    def on_turn_completed(
        self,
        session_id: str,
        response_id: str | None,
        messages: list[AgentMessage],
    ) -> None:
        """Run after the terminal-idle receipts have been persisted."""
        for message in messages:
            try:
                self._maybe_advance(session_id, response_id, message)
            except Exception:  # noqa: BLE001 - bridge is best-effort
                _logger.warning(
                    "workflow auto-advance failed for message %s in %s",
                    message.message_id,
                    session_id,
                    exc_info=True,
                )

    def _maybe_advance(
        self,
        session_id: str,
        response_id: str | None,
        message: AgentMessage,
    ) -> None:
        if not message.run_id or not message.task_id or not message.recipient_role:
            return
        if self.conversation_store is None:
            return
        task = self.engine.store.get_task(message.task_id)
        if task is None or task.run_id != message.run_id:
            return
        if task.status not in ("assigned", "running", "waiting_review"):
            return
        page = self.conversation_store.list_items(
            session_id,
            limit=20,
            order="desc",
            type="message",
        )
        text = assistant_text_from_items(page, response_id)
        outcome, review_decision = declared_outcome(
            text,
            task.assignee_role,
            message.intent,
        )
        if outcome is None:
            _logger.info(
                "workflow stage %s message %s has no declared result; keeping manual gate",
                message.task_id,
                message.message_id,
            )
            return
        asyncio.run(
            self.engine.advance(
                run_id=message.run_id,
                task_id=message.task_id,
                outcome=outcome,
                review_decision=review_decision,
            )
        )
        _logger.info(
            "workflow auto-advanced run %s task %s to %s",
            message.run_id,
            message.task_id,
            outcome,
        )
