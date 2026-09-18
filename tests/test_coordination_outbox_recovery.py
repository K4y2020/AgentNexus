"""Recovery tests for outbox leases abandoned by a crashed dispatch.

These cover the failure the happy-path suite cannot see: a claim writes
``leased`` before injection and only the delivery attempt clears it, so a
process that dies in between used to strand the row forever — never re-claimed,
never reconciled, and never surfaced anywhere.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agentnexus.coordination.probe import ProbeVerdict, probe_delivery_state
from agentnexus.coordination.reconciliation import EVENT_TYPE, reconcile_effect_unknown
from agentnexus.coordination.store import CoordinationStore
from agentnexus.coordination.types import (
    DELIVERY_ERROR_REJECTED,
    DELIVERY_ERROR_UNPROVEN,
    DELIVERY_ERROR_UNREACHABLE,
    AgentMessage,
    DeliveryAttempt,
    OutboxItem,
)
from agentnexus.coordination.workflow_engine import _delivery_in_flight

# Far enough ahead that every lease in the fixture counts as stale without
# sleeping or rewriting persisted timestamps.
FUTURE = 10_000.0


@pytest.fixture
def memory_store(tmp_path: Path) -> CoordinationStore:
    return CoordinationStore(tmp_path / "outbox_recovery_test.db")


def _message(**overrides: object) -> AgentMessage:
    kwargs: dict[str, object] = {
        "root_session_id": "conv_root_recover",
        "sender_session_id": "conv_sender",
        "sender_role": "planner",
        "recipient_session_id": "conv_receiver",
        "recipient_role": "implementer",
        "intent": "task.request",
        "payload": {"prompt": "Apply the plan"},
    }
    kwargs.update(overrides)
    return AgentMessage(**kwargs)  # type: ignore[arg-type]


def _claim_and_crash(store: CoordinationStore, message: AgentMessage) -> OutboxItem:
    """Simulate a dispatcher that claimed a row and died before the attempt."""
    _, outbox = store.save_message_and_outbox(message)
    claimed = store.claim_pending_outbox(limit=10)
    assert [item.item_id for item in claimed] == [outbox.item_id]
    assert claimed[0].status == "leased"
    return outbox


def test_crash_before_attempt_is_escalated_not_silently_stranded(memory_store):
    """The core regression: a stranded lease must raise an alarm."""
    outbox = _claim_and_crash(memory_store, _message())

    results = memory_store.reclaim_stale_outbox_leases(now=time.time() + FUTURE)

    assert [r.outcome for r in results] == ["unknown"]
    # No longer leased, so it is at least off the invisible-stall path.
    assert memory_store.get_outbox_item(outbox.item_id).status == "unknown"
    stored = memory_store.get_message(outbox.message_id)
    assert stored is not None
    # The point of the fix: a human can see this instead of waiting forever.
    assert stored.effect_unknown_reason, "a stranded delivery must warn, not wait silently"
    assert "delivery_lease_expired_before_attempt" in stored.effect_unknown_reason


def test_unknown_delivery_state_is_actually_written(memory_store):
    """``unknown`` was declared in the enum but nothing ever wrote it."""
    outbox = _claim_and_crash(memory_store, _message())

    memory_store.reclaim_stale_outbox_leases(now=time.time() + FUTURE)

    attempts = memory_store.list_delivery_attempts(outbox.message_id)
    assert [a.delivery_state for a in attempts] == ["unknown"]
    events = [
        e for e in memory_store.list_events("conv_root_recover") if e.event_type == EVENT_TYPE
    ]
    assert len(events) == 1
    assert events[0].payload["message_id"] == outbox.message_id


def test_provably_undelivered_lease_is_replayed(memory_store):
    """A request that provably never landed may be retried."""
    outbox = _claim_and_crash(memory_store, _message())
    memory_store.record_delivery_attempt(
        DeliveryAttempt(
            message_id=outbox.message_id,
            target_session_id=outbox.target_session_id,
            delivery_mode="offline",
            delivery_state="failed",
            error=f"{DELIVERY_ERROR_UNREACHABLE}: runner offline",
        ),
        False,
        outbox.item_id,
    )

    results = memory_store.reclaim_stale_outbox_leases(
        now=time.time() + FUTURE, next_retry_delay_s=0
    )

    assert [r.outcome for r in results] == ["requeued"]
    assert memory_store.get_outbox_item(outbox.item_id).status == "pending"
    # The original bug: claim_pending_outbox only ever matched "pending".
    reclaimed = memory_store.claim_pending_outbox(limit=10)
    assert [item.item_id for item in reclaimed] == [outbox.item_id]


@pytest.mark.parametrize(
    "error",
    [
        f"{DELIVERY_ERROR_UNPROVEN}: runner returned 503",
        "legacy error written before the taxonomy existed",
    ],
)
def test_unproven_failures_are_never_replayed(memory_store, error):
    """A request that may have landed must not be re-sent."""
    outbox = _claim_and_crash(memory_store, _message())
    memory_store.record_delivery_attempt(
        DeliveryAttempt(
            message_id=outbox.message_id,
            target_session_id=outbox.target_session_id,
            delivery_mode="offline",
            delivery_state="failed",
            error=error,
        ),
        False,
        outbox.item_id,
    )

    results = memory_store.reclaim_stale_outbox_leases(now=time.time() + FUTURE)

    assert [r.outcome for r in results] == ["unknown"]
    assert memory_store.get_outbox_item(outbox.item_id).status == "unknown"


def test_rejected_request_is_safe_to_replay(memory_store):
    outbox = _claim_and_crash(memory_store, _message())
    memory_store.record_delivery_attempt(
        DeliveryAttempt(
            message_id=outbox.message_id,
            target_session_id=outbox.target_session_id,
            delivery_mode="offline",
            delivery_state="failed",
            error=f"{DELIVERY_ERROR_REJECTED}: runner returned 404",
        ),
        False,
        outbox.item_id,
    )

    results = memory_store.reclaim_stale_outbox_leases(
        now=time.time() + FUTURE, next_retry_delay_s=0
    )

    assert results[0].outcome == "requeued"


def test_replay_safe_but_retry_exhausted_still_needs_attention(memory_store):
    """Proven harmless is not the same as resolved.

    The request provably never reached the runner, so there is no side effect
    to fear — but the retry budget is gone, so nothing will ever deliver the
    stage. It must still be surfaced rather than dropped.
    """
    outbox = _claim_and_crash(memory_store, _message())
    memory_store.record_delivery_attempt(
        DeliveryAttempt(
            message_id=outbox.message_id,
            target_session_id=outbox.target_session_id,
            delivery_mode="offline",
            delivery_state="failed",
            error=f"{DELIVERY_ERROR_REJECTED}: runner returned 404",
        ),
        False,
        outbox.item_id,
    )

    results = memory_store.reclaim_stale_outbox_leases(
        now=time.time() + FUTURE, max_retries=1, next_retry_delay_s=0
    )

    assert [r.outcome for r in results] == ["abandoned"]
    assert memory_store.get_outbox_item(outbox.item_id).status == "failed"
    # Surfaces on the same channel as a genuine unknown, but the reason says
    # there is no side effect, so an operator does not go hunting for one.
    assert "never reached the runner" in results[0].reason
    assert memory_store.get_message(outbox.message_id).effect_unknown_reason is not None


def test_fresh_leases_are_left_alone(memory_store):
    """A dispatch that is merely slow must not be preempted."""
    outbox = _claim_and_crash(memory_store, _message())

    results = memory_store.reclaim_stale_outbox_leases(now=time.time())

    assert results == []
    assert memory_store.get_outbox_item(outbox.item_id).status == "leased"


def test_queued_message_is_reconciled(memory_store):
    """``queued`` hangs used to fall outside the candidate set entirely."""
    message = _message()
    memory_store.save_message_and_outbox(message)
    assert memory_store.get_message(message.message_id).message_state == "queued"

    report = reconcile_effect_unknown(memory_store, now=time.time() + FUTURE, grace_s=1.0)

    assert report.marked == [message.message_id]
    stored = memory_store.get_message(message.message_id)
    assert stored is not None
    assert "delivery_never_attempted" in (stored.effect_unknown_reason or "")


def test_recently_queued_message_is_not_marked(memory_store):
    """Ordinary in-flight traffic must not be flagged as unknown."""
    message = _message()
    memory_store.save_message_and_outbox(message)

    report = reconcile_effect_unknown(memory_store, grace_s=300.0)

    assert report.marked == []


@pytest.mark.asyncio
async def test_probe_is_conservative_without_a_runner():
    """With nothing to ask, the probe must not invent an answer."""
    assert await probe_delivery_state("conv_nonexistent") is ProbeVerdict.UNKNOWN


@pytest.mark.parametrize(
    ("outbox_statuses", "expected"),
    [
        (["pending"], True),
        (["leased"], True),
        (["confirmed"], True),  # injected, waiting to be consumed
        (["failed"], False),  # terminal: nothing will retry it
        (["unknown"], False),
        ([], False),  # row gone altogether
    ],
)
def test_delivery_in_flight_matrix(outbox_statuses, expected):
    message = AgentMessage(consumption_state="unconsumed")
    items = [OutboxItem(status=status) for status in outbox_statuses]  # type: ignore[arg-type]
    assert _delivery_in_flight(message, items) is expected


def test_consumed_message_is_never_healed():
    message = AgentMessage(consumption_state="consumed")
    assert _delivery_in_flight(message, []) is True
