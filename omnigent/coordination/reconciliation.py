"""Effect-unknown reconciliation for control-plane message delivery.

A runner POST is only proof of injection, not of consumption. This module
walks active, unconsumed messages whose latest confirmed (or explicitly
``unknown``) delivery attempt has gone silent for
:data:`EFFECT_UNKNOWN_GRACE_S`, stamps ``effect_unknown_reason`` once, and
publishes an immutable ``effect.unknown_detected`` timeline event. The stamp
covers the plan's Gate C invariant: the control plane never guesses a
side-effect happened; it records "injected but not confirmed" as unknown so
the run can surface an explicit reconciliation path instead of a fake
``confirmed`` unit of work.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from omnigent.coordination.types import (
    AgentMessage,
    CoordinationEvent,
    DeliveryAttempt,
)

if TYPE_CHECKING:
    from omnigent.coordination.store import CoordinationStore

_logger = logging.getLogger(__name__)

#: How long after a confirmed/unknown delivery attempt a message may stay
#: unconsumed before the reconciler declares its effect unknown.
EFFECT_UNKNOWN_GRACE_S = 300.0

#: Event emitted once per newly-stamped message.
EVENT_TYPE = "effect.unknown_detected"


@dataclass
class EffectUnknownReport:
    """Result of one reconciliation scan."""

    scanned: int = 0
    marked: list[str] = field(default_factory=list)
    already_marked: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned": self.scanned,
            "marked": self.marked,
            "already_marked": self.already_marked,
            "errors": self.errors,
        }


def reason_for_unknown(
    message: AgentMessage,
    attempts: list[DeliveryAttempt],
    *,
    now: float,
    grace_s: float,
) -> str | None:
    """Return a stable reason when a message's effect is unknown, else None.

    Only the latest attempt matters: an old confirmed delivery followed by a
    fresh failed/retryable attempt is still being worked, and a fresh
    confirmed attempt resets the grace clock. The latest attempt must be
    ``confirmed`` (runner accepted the injection) or explicitly ``unknown``
    (the adapter lost the answer) and at least ``grace_s`` old.

    A message with no attempt at all is unknown too once it is old enough:
    the dispatch claimed it and vanished, so nothing records whether the
    runner ever saw it. Escalating beats waiting silently.
    """
    if message.consumption_state != "unconsumed":
        return None
    if message.message_state not in ("queued", "active"):
        return None
    if not attempts:
        age_s = now - message.updated_at
        if age_s < grace_s:
            return None
        return (
            f"delivery_never_attempted: message has been {message.message_state} and "
            f"unconsumed for {age_s:.0f}s with no delivery attempt on record"
        )
    latest = attempts[-1]
    age_s = now - latest.updated_at
    if age_s < grace_s:
        return None
    if latest.delivery_state == "confirmed":
        return (
            f"injected_but_unconsumed: delivery {latest.attempt_id} confirmed "
            f"{age_s:.0f}s ago with no consumption receipt"
        )
    if latest.delivery_state == "unknown":
        return (
            f"delivery_unknown: delivery {latest.attempt_id} left unsolved "
            f"{age_s:.0f}s ago with no consumption receipt"
        )
    return None


def reconcile_effect_unknown(
    store: CoordinationStore,
    *,
    now: float | None = None,
    grace_s: float = EFFECT_UNKNOWN_GRACE_S,
    limit: int = 200,
) -> EffectUnknownReport:
    """Scan unconsumed messages and stamp effect-unknown reasons."""
    now = time.time() if now is None else now
    report = EffectUnknownReport()
    for message in store.list_effect_unknown_candidates(limit=limit):
        report.scanned += 1
        try:
            attempts = store.list_delivery_attempts(message.message_id)
            reason = reason_for_unknown(message, attempts, now=now, grace_s=grace_s)
            if reason is None:
                continue
            if store.mark_message_effect_unknown(message.message_id, reason) is None:
                report.already_marked += 1
                continue
            report.marked.append(message.message_id)
            store.record_event(
                CoordinationEvent(
                    root_session_id=message.root_session_id,
                    run_id=message.run_id,
                    task_id=message.task_id,
                    actor_session_id=message.recipient_session_id,
                    event_type=EVENT_TYPE,
                    payload={
                        "message_id": message.message_id,
                        "reason": reason,
                        "latest_attempt_id": attempts[-1].attempt_id if attempts else None,
                        "consumer_session_id": message.recipient_session_id,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{message.message_id}: {exc}")
            _logger.warning(
                "effect-unknown reconciliation failed for %s: %s",
                message.message_id,
                exc,
            )
    if report.marked:
        _logger.info(
            "effect-unknown reconciliation marked %d messages (scanned %d)",
            len(report.marked),
            report.scanned,
        )
    return report
