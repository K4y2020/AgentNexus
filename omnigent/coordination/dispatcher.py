"""Capability-aware message dispatcher for AgentNexus Multi-Agent Outbox."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.types import AgentMessage, DeliveryAttempt, DeliveryMode

_logger = logging.getLogger(__name__)


class CoordinationDispatcher:
    """Dispatches durable outbox messages to target sessions based on harness capability."""

    def __init__(self, store: CoordinationStore) -> None:
        self.store = store
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background outbox polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        _logger.info("CoordinationDispatcher started")

    async def stop(self) -> None:
        """Stop the background outbox polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        _logger.info("CoordinationDispatcher stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self.dispatch_once()
            except Exception:
                _logger.exception("Error in coordination dispatch loop")
            await asyncio.sleep(0.5)

    async def dispatch_once(self) -> int:
        """Process one batch of pending outbox messages. Returns number of processed items."""
        items = await asyncio.to_thread(self.store.fetch_pending_outbox, limit=20)
        if not items:
            return 0

        for item in items:
            try:
                msg_dict = json.loads(item.payload_json)
                msg = AgentMessage(**msg_dict)
                await self._deliver_message(item.item_id, msg)
            except Exception as exc:  # noqa: BLE001
                _logger.error("Failed to deliver outbox item %s: %s", item.item_id, exc)
                attempt = DeliveryAttempt(
                    message_id=item.message_id,
                    target_session_id=item.target_session_id,
                    delivery_mode="offline",
                    delivery_state="failed",
                    error=str(exc),
                    attempt_count=item.retry_count + 1,
                )
                await asyncio.to_thread(self.store.record_delivery_attempt, attempt, False)
        return len(items)

    async def _deliver_message(self, _outbox_item_id: str, msg: AgentMessage) -> None:
        """Deliver one message through RunnerRouter to target runner and record receipt."""
        delivered_mode: DeliveryMode = "next_turn"
        receipt: dict[str, Any] = {"status": "inbox_queued"}

        # 1. Attempt real delivery to live target Runner via RunnerRouter
        try:
            from omnigent.server.routes._sessions.common import get_server_runner_router

            router = get_server_runner_router()
            if router is not None:
                routed = router.client_for_session_resources(msg.recipient_session_id)
                prompt_text = (
                    msg.payload.get("prompt")
                    or msg.payload.get("instruction")
                    or json.dumps(msg.payload)
                )
                prefix = f"[A2A {msg.intent} from {msg.sender_role}]: "
                event_payload = {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "text", "text": f"{prefix}{prompt_text}"}],
                }
                resp = await routed.client.post(
                    f"/v1/sessions/{msg.recipient_session_id}/events",
                    json=event_payload,
                    timeout=10.0,
                )
                if resp.status_code < 400:
                    delivered_mode = "live"
                    receipt = {"status": "runner_injected", "status_code": resp.status_code}
        except Exception as exc:  # noqa: BLE001
            _logger.debug("Runner injection skipped for %s: %s", msg.recipient_session_id, exc)

        # 2. Publish to session_stream for live Web UI timeline
        try:
            from omnigent.runtime import session_stream as _session_stream

            _session_stream.publish(
                msg.recipient_session_id,
                {
                    "type": "coordination.message.received",
                    "message_id": msg.message_id,
                    "sender": msg.sender_session_id,
                    "sender_role": msg.sender_role,
                    "intent": msg.intent,
                    "payload": msg.payload,
                    "artifacts": msg.artifacts,
                },
            )
            if delivered_mode != "live":
                delivered_mode = "next_turn"
                receipt = {"status": "stream_published"}
        except Exception:  # noqa: BLE001
            _logger.debug("Session stream publish skipped: %s", msg.recipient_session_id)

        attempt = DeliveryAttempt(
            message_id=msg.message_id,
            target_session_id=msg.recipient_session_id,
            delivery_mode=delivered_mode,
            delivery_state="confirmed",
            injection_receipt=receipt,
            attempt_count=1,
        )
        await asyncio.to_thread(self.store.record_delivery_attempt, attempt, True)
