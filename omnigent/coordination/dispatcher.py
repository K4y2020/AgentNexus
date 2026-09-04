"""Capability-aware message dispatcher for AgentNexus Multi-Agent Outbox."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from omnigent.coordination.probe import ProbeVerdict, probe_delivery_state
from omnigent.coordination.reconciliation import reconcile_effect_unknown
from omnigent.coordination.store import CoordinationStore, _replay_is_safe
from omnigent.coordination.types import (
    DELIVERY_ERROR_REJECTED,
    DELIVERY_ERROR_UNPROVEN,
    DELIVERY_ERROR_UNREACHABLE,
    AgentMessage,
    DeliveryAttempt,
    OutboxItem,
    OutboxReclaim,
)
from omnigent.db.db_models import InvalidUuidError
from omnigent.errors import OmnigentError

if TYPE_CHECKING:
    from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)


def _classify_delivery_error(exc: BaseException) -> str:
    """Tag a delivery failure with whether the request provably never landed.

    The tag is persisted with the delivery attempt so a later recovery pass
    can tell a safe replay from a possible double-injection. Anything not
    positively proven to have missed the runner is treated as unproven.
    """
    if isinstance(exc, InvalidUuidError):
        return DELIVERY_ERROR_REJECTED
    # A missing router or an unbound/offline runner means the HTTP call was
    # never made.
    if isinstance(exc, OmnigentError) or "no server runner router configured" in str(exc):
        return DELIVERY_ERROR_UNREACHABLE
    try:
        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return DELIVERY_ERROR_UNREACHABLE
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int):
            return DELIVERY_ERROR_REJECTED if status < 500 else DELIVERY_ERROR_UNPROVEN
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        pass
    # The dispatcher's own wording for a non-2xx runner reply, e.g.
    # "runner rejected A2A delivery with status 503: ...".
    marker = "with status "
    text = str(exc)
    if marker in text:
        code = text.split(marker, 1)[1].split(":", 1)[0].strip()
        if code.isdigit():
            return DELIVERY_ERROR_REJECTED if int(code) < 500 else DELIVERY_ERROR_UNPROVEN
    return DELIVERY_ERROR_UNPROVEN


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
        # Probe the runner before replaying a crashed delivery. Tests that
        # want deterministic offline behavior turn this off.
        self.probe_delivery = True

    async def start(self) -> None:
        """Start the background outbox polling loop."""
        if self._running:
            return
        self._running = True
        self._poll_count = 0
        # A claim writes `leased` and only the attempt record clears it, so a
        # crash between the two strands the row. Sweep before the first claim
        # so a restart never inherits an invisible stall.
        try:
            await self.reclaim_stale_leases_once()
        except Exception:
            _logger.exception("Error reclaiming stale outbox leases at startup")
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
                    await self.reclaim_stale_leases_once()
                except Exception:
                    _logger.exception("Error reclaiming stale outbox leases")
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

    async def reclaim_stale_leases_once(
        self, *, lease_timeout_s: float | None = None
    ) -> list[OutboxReclaim]:
        """Resolve outbox leases abandoned by a crashed dispatch.

        Rows are listed, probed, then resolved one at a time so the probe —
        which has to ask the runner — runs on the event loop while the store
        writes stay on threads.
        """
        kwargs: dict[str, object] = {}
        if lease_timeout_s is not None:
            kwargs["lease_timeout_s"] = lease_timeout_s
        stale = await asyncio.to_thread(self.store.list_stale_outbox_leases, **kwargs)
        if not stale:
            return []
        result: list[OutboxReclaim] = []
        for item in stale:
            replay_allowed = await self._replay_verdict(item)
            result.append(
                await asyncio.to_thread(
                    self.store.resolve_stale_lease, item, replay_allowed=replay_allowed
                )
            )
        if result:
            _logger.warning(
                "reclaimed %d stale outbox lease(s): %s",
                len(result),
                ", ".join(f"{r.item_id}={r.outcome}" for r in result),
            )
        return result

    async def _replay_verdict(self, item: OutboxItem) -> bool | None:
        """Decide whether a stale lease may be replayed.

        Returns True/False to force a decision, or None to let the store fall
        back to the persisted attempt, which errs towards escalation.
        """
        attempts = await asyncio.to_thread(self.store.list_delivery_attempts, item.message_id)
        if _replay_is_safe(attempts[-1] if attempts else None):
            return True
        if not self.probe_delivery:
            return None
        verdict = await probe_delivery_state(item.target_session_id)
        if verdict is ProbeVerdict.NOT_INJECTED:
            return True
        if verdict is ProbeVerdict.INJECTED:
            return False
        return None

    async def dispatch_once(self) -> int:
        """Process one batch of pending outbox messages. Returns number processed."""
        items = await asyncio.to_thread(self.store.claim_pending_outbox, limit=20)
        if not items:
            return 0

        for item in items:
            try:
                msg_dict = json.loads(item.payload_json)
                msg = AgentMessage(**msg_dict)
                if self._message_expired(msg):
                    await asyncio.to_thread(
                        self.store.expire_message,
                        msg.message_id,
                        "message TTL elapsed before delivery",
                    )
                    continue
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
                    error=f"{_classify_delivery_error(exc)}: {exc}",
                    attempt_count=item.retry_count + 1,
                )
                await asyncio.to_thread(
                    self.store.record_delivery_attempt, attempt, False, item.item_id
                )
                await asyncio.to_thread(self.store.requeue_outbox, item.item_id)
        return len(items)

    def _message_expired(self, msg: AgentMessage) -> bool:
        """Return whether a message's TTL elapsed before injection."""
        if msg.ttl_seconds is None:
            return False
        return time.time() - msg.created_at > msg.ttl_seconds

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
                if recipient is None:
                    # G2 pre-check: recipient conversation does not exist.
                    # Permanently fail immediately without wasting retry slots.
                    _logger.warning(
                        "A2A recipient conversation %s does not exist; "
                        "failing permanently for message %s",
                        msg.recipient_session_id,
                        msg.message_id,
                    )
                    attempt = DeliveryAttempt(
                        message_id=msg.message_id,
                        target_session_id=msg.recipient_session_id,
                        target_sequence=target_sequence,
                        target_harness=None,
                        delivery_mode="offline",
                        delivery_state="failed",
                        error_code="CONVERSATION_NOT_FOUND",
                        error=(
                            f"{DELIVERY_ERROR_REJECTED}: conversation "
                            f"'{msg.recipient_session_id}' not found"
                        ),
                        attempt_count=attempt_count,
                    )
                    await asyncio.to_thread(
                        self.store.record_delivery_attempt, attempt, True, outbox_item_id
                    )
                    await asyncio.to_thread(
                        self.store.update_message_state, msg.message_id, "failed"
                    )
                    self._publish_timeline(msg)
                    return False

                if not getattr(recipient, "runner_id", None):
                    _logger.warning(
                        "A2A recipient conversation %s is not bound to a runner; "
                        "failing attempt for message %s",
                        msg.recipient_session_id,
                        msg.message_id,
                    )
                    attempt = DeliveryAttempt(
                        message_id=msg.message_id,
                        target_session_id=msg.recipient_session_id,
                        target_sequence=target_sequence,
                        target_harness=None,
                        delivery_mode="offline",
                        delivery_state="failed",
                        error_code="RUNNER_UNBOUND",
                        error=(
                            f"{DELIVERY_ERROR_UNREACHABLE}: conversation "
                            f"'{msg.recipient_session_id}' is not bound to a runner; "
                            "resume the session to bind a registered runner"
                        ),
                        attempt_count=attempt_count,
                    )
                    await asyncio.to_thread(
                        self.store.record_delivery_attempt, attempt, False, outbox_item_id
                    )
                    await asyncio.to_thread(self.store.requeue_outbox, outbox_item_id)
                    self._publish_timeline(msg)
                    return False

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
            content: list[dict[str, Any]] = [
                {"type": "input_text", "text": f"{prefix}{prompt_text}"}
            ]
            for artifact in msg.artifacts:
                artifact_type = artifact.get("type")
                file_id = artifact.get("file_id")
                if artifact_type not in ("input_file", "input_image") or not isinstance(
                    file_id, str
                ):
                    continue
                block: dict[str, Any] = {"type": artifact_type, "file_id": file_id}
                filename = artifact.get("filename")
                if isinstance(filename, str) and filename:
                    block["filename"] = filename
                content.append(block)
            event_payload = {
                "type": "message",
                "role": "user",
                "content": content,
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
                error_code="INVALID_RECIPIENT_ID",
                error=f"{DELIVERY_ERROR_REJECTED}: invalid recipient session id: {exc}",
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
            classified = _classify_delivery_error(exc)
            err_str = str(exc).lower()
            if "offline" in err_str or "connect" in err_str:
                err_code = "RUNNER_OFFLINE"
            elif "not bound" in err_str or "unbound" in err_str:
                err_code = "RUNNER_UNBOUND"
            elif classified == DELIVERY_ERROR_REJECTED:
                err_code = "RUNNER_REJECTED"
            else:
                err_code = "DELIVERY_UNREACHABLE"

            attempt = DeliveryAttempt(
                message_id=msg.message_id,
                target_session_id=msg.recipient_session_id,
                target_sequence=target_sequence,
                target_harness=None,
                delivery_mode="offline",
                delivery_state="failed",
                error_code=err_code,
                error=f"{classified}: {exc}",
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
