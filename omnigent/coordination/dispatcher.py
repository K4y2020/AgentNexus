"""Capability-aware message dispatcher for AgentNexus Multi-Agent Outbox."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from omnigent.coordination.reconciliation import reconcile_effect_unknown
from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.types import AgentMessage, DeliveryAttempt
from omnigent.db.db_models import InvalidUuidError

if TYPE_CHECKING:
    from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)


class CoordinationDispatcher:
    """Dispatches durable outbox messages to target sessions based on harness capability."""

    def __init__(
        self,
        store: CoordinationStore,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        self.store = store
        self.conversation_store = conversation_store
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # Reconciliation cadence: every N poll loops (~30s at 0.5s poll).
        self._poll_count = 0
        self.reconcile_every_loops = 60

    async def start(self) -> None:
        """Start the background outbox polling loop."""
        if self._running:
            return
        self._running = True
        self._poll_count = 0
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
            self._poll_count += 1
            if self._poll_count % self.reconcile_every_loops == 0:
                try:
                    await self.reconcile_once()
                except Exception:
                    _logger.exception("Error in effect-unknown reconciliation")
            await asyncio.sleep(0.5)

    async def reconcile_once(self, *, grace_s: float | None = None) -> object:
        """Run one effect-unknown reconciliation scan and return its report."""
        kwargs: dict[str, object] = {}
        if grace_s is not None:
            kwargs["grace_s"] = grace_s
        return await asyncio.to_thread(reconcile_effect_unknown, self.store, **kwargs)

    async def dispatch_once(self) -> int:
        """Process one batch of pending outbox messages. Returns number processed."""
        items = await asyncio.to_thread(self.store.claim_pending_outbox, limit=20)
        if not items:
            return 0

        for item in items:
            try:
                msg_dict = json.loads(item.payload_json)
                msg = AgentMessage(**msg_dict)
                await self._deliver_message(
                    item.item_id,
                    msg,
                    attempt_count=item.retry_count + 1,
                    target_sequence=item.target_sequence,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.error("Failed to deliver outbox item %s: %s", item.item_id, exc)
                attempt = DeliveryAttempt(
                    message_id=item.message_id,
                    target_session_id=item.target_session_id,
                    target_sequence=item.target_sequence,
                    delivery_mode="offline",
                    delivery_state="failed",
                    error=str(exc),
                    attempt_count=item.retry_count + 1,
                )
                await asyncio.to_thread(
                    self.store.record_delivery_attempt, attempt, False, item.item_id
                )
                await asyncio.to_thread(self.store.requeue_outbox, item.item_id)
        return len(items)

    async def _deliver_message(
        self,
        outbox_item_id: str,
        msg: AgentMessage,
        *,
        attempt_count: int,
        target_sequence: int | None,
    ) -> bool:
        """Deliver one message through RunnerRouter; only a 2xx may confirm it."""
        try:
            from omnigent.server.routes._sessions.common import get_server_runner_router

            router = get_server_runner_router()
            if router is None:
                raise RuntimeError("no server runner router configured")
            routed = router.client_for_session_resources(msg.recipient_session_id)
            recipient = None
            if self.conversation_store is not None:
                recipient = await asyncio.to_thread(
                    self.conversation_store.get_conversation,
                    msg.recipient_session_id,
                )
                # The runner only publishes turn events through a server-side SSE
                # relay. Without a subscription the dispatched turn completes in
                # the runner but its terminal session.status never lands here, so
                # the durable message can never be marked consumed. Subscribe
                # before injecting (the normal events path does the same).
                from omnigent.server.routes._sessions.orchestration import (
                    _ensure_runner_relay_ready,
                )

                await _ensure_runner_relay_ready(
                    msg.recipient_session_id,
                    recipient.runner_id,
                    routed.client,
                    self.conversation_store,
                )
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
                "metadata": {
                    "a2a": True,
                    "message_id": msg.message_id,
                    "sender_session_id": msg.sender_session_id,
                    "sender_role": msg.sender_role,
                    "intent": msg.intent,
                    "correlation_id": msg.correlation_id,
                },
            }
            if recipient is not None:
                event_payload["agent_id"] = recipient.agent_id
                event_payload["model"] = recipient.agent_id or ""
            resp = await routed.client.post(
                f"/v1/sessions/{msg.recipient_session_id}/events",
                json=event_payload,
                timeout=10.0,
            )
            if not 200 <= resp.status_code < 300:
                raise RuntimeError(
                    f"runner rejected A2A delivery with status {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            receipt: dict[str, Any] = {
                "status": "runner_injected",
                "status_code": resp.status_code,
                "runner_id": routed.runner_id,
            }
        except InvalidUuidError as exc:
            # A syntactically invalid session id can never be delivered; fail
            # it permanently instead of hot-looping the outbox retry timer.
            _logger.warning(
                "A2A recipient %s is not a valid session id; failing message %s: %s",
                msg.recipient_session_id,
                msg.message_id,
                exc,
            )
            attempt = DeliveryAttempt(
                message_id=msg.message_id,
                target_session_id=msg.recipient_session_id,
                target_sequence=target_sequence,
                target_harness=None,
                delivery_mode="offline",
                delivery_state="failed",
                error=f"invalid recipient session id: {exc}",
                attempt_count=attempt_count,
            )
            await asyncio.to_thread(
                self.store.record_delivery_attempt, attempt, True, outbox_item_id
            )
            self._publish_timeline(msg)
            return False
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "A2A delivery failed for message %s -> %s: %s",
                msg.message_id,
                msg.recipient_session_id,
                exc,
            )
            attempt = DeliveryAttempt(
                message_id=msg.message_id,
                target_session_id=msg.recipient_session_id,
                target_sequence=target_sequence,
                target_harness=None,
                delivery_mode="offline",
                delivery_state="failed",
                error=str(exc),
                attempt_count=attempt_count,
            )
            await asyncio.to_thread(
                self.store.record_delivery_attempt, attempt, False, outbox_item_id
            )
            await asyncio.to_thread(self.store.requeue_outbox, outbox_item_id)
            self._publish_timeline(msg)
            return False

        attempt = DeliveryAttempt(
            message_id=msg.message_id,
            target_session_id=msg.recipient_session_id,
            target_sequence=target_sequence,
            target_harness=None,
            delivery_mode="live",
            delivery_state="confirmed",
            injection_receipt=receipt,
            attempt_count=attempt_count,
        )
        await asyncio.to_thread(self.store.record_delivery_attempt, attempt, True, outbox_item_id)
        await asyncio.to_thread(self.store.update_message_state, msg.message_id, "active")
        self._publish_timeline(msg)
        return True

    def _publish_timeline(self, msg: AgentMessage) -> None:
        """Publish the UI timeline event without influencing delivery state."""
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
        except Exception:  # noqa: BLE001
            _logger.debug("Session stream publish skipped: %s", msg.recipient_session_id)
