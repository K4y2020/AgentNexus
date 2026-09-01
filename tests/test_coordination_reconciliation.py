"""Tests for effect-unknown reconciliation of injected agent messages."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from omnigent.coordination.dispatcher import CoordinationDispatcher
from omnigent.coordination.reconciliation import EVENT_TYPE, reconcile_effect_unknown
from omnigent.coordination.store import CoordinationStore
from omnigent.coordination.types import AgentMessage, DeliveryAttempt


@pytest.fixture
def memory_store(tmp_path: Path) -> CoordinationStore:
    return CoordinationStore(tmp_path / "reconciliation_test.db")


def _active_message(
    store: CoordinationStore,
    *,
    root_session_id: str = "conv_root_recon",
    recipient_session_id: str = "conv_receiver",
) -> AgentMessage:
    msg = AgentMessage(
        root_session_id=root_session_id,
        sender_session_id="conv_sender",
        sender_role="planner",
        recipient_session_id=recipient_session_id,
        recipient_role="implementer",
        intent="task.request",
        payload={"prompt": "Apply the plan"},
    )
    store.save_message_and_outbox(msg)
    store.update_message_state(msg.message_id, "active")
    return msg


def _old_attempt(
    msg: AgentMessage,
    *,
    state: str,
    age_s: float,
    sequence: int = 1,
) -> DeliveryAttempt:
    stamped_at = time.time() - age_s
    return DeliveryAttempt(
        message_id=msg.message_id,
        target_session_id=msg.recipient_session_id,
        target_sequence=sequence,
        delivery_mode="live",
        delivery_state=state,  # type: ignore[arg-type]
        injection_receipt={"status": "runner_injected"} if state == "confirmed" else None,
        created_at=stamped_at,
        updated_at=stamped_at,
    )


def test_reconcile_marks_confirmed_but_unconsumed_after_grace(
    memory_store: CoordinationStore,
) -> None:
    msg = _active_message(memory_store)
    memory_store.record_delivery_attempt(
        _old_attempt(msg, state="confirmed", age_s=301.0),
        mark_outbox_done=True,
    )

    report = reconcile_effect_unknown(memory_store, now=time.time(), grace_s=300.0)

    assert report.scanned == 1
    assert report.marked == [msg.message_id]
    current = memory_store.get_message(msg.message_id)
    assert current is not None
    assert current.effect_unknown_reason is not None
    assert current.effect_unknown_reason.startswith("injected_but_unconsumed:")

    events = [
        event
        for event in memory_store.list_events(msg.root_session_id)
        if event.event_type == EVENT_TYPE
    ]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["message_id"] == msg.message_id
    assert payload["consumer_session_id"] == msg.recipient_session_id


def test_reconcile_does_not_mark_within_grace(memory_store: CoordinationStore) -> None:
    msg = _active_message(memory_store)
    memory_store.record_delivery_attempt(
        _old_attempt(msg, state="confirmed", age_s=10.0),
        mark_outbox_done=True,
    )

    report = reconcile_effect_unknown(memory_store, now=time.time(), grace_s=300.0)

    assert report.marked == []
    assert memory_store.get_message(msg.message_id).effect_unknown_reason is None
    assert all(
        event.event_type != EVENT_TYPE
        for event in memory_store.list_events(msg.root_session_id)
    )


@pytest.mark.parametrize("state", ["failed", "pending", "leased", "injected"])
def test_reconcile_ignores_nonconfirmed_latest_attempt(
    memory_store: CoordinationStore,
    state: str,
) -> None:
    msg = _active_message(memory_store)
    memory_store.record_delivery_attempt(
        _old_attempt(msg, state="confirmed", age_s=400.0, sequence=1),
        mark_outbox_done=True,
    )
    memory_store.record_delivery_attempt(
        _old_attempt(msg, state=state, age_s=1.0, sequence=2),
        mark_outbox_done=False,
    )

    report = reconcile_effect_unknown(memory_store, now=time.time(), grace_s=300.0)

    assert report.marked == []
    assert memory_store.get_message(msg.message_id).effect_unknown_reason is None


def test_reconcile_marks_explicit_unknown_after_grace(
    memory_store: CoordinationStore,
) -> None:
    msg = _active_message(memory_store)
    memory_store.record_delivery_attempt(
        _old_attempt(msg, state="unknown", age_s=301.0),
        mark_outbox_done=True,
    )

    report = reconcile_effect_unknown(memory_store, now=time.time(), grace_s=300.0)

    assert report.marked == [msg.message_id]
    reason = memory_store.get_message(msg.message_id).effect_unknown_reason
    assert reason is not None
    assert reason.startswith("delivery_unknown:")


def test_reconcile_is_idempotent_and_emits_event_once(
    memory_store: CoordinationStore,
) -> None:
    msg = _active_message(memory_store)
    memory_store.record_delivery_attempt(
        _old_attempt(msg, state="confirmed", age_s=301.0),
        mark_outbox_done=True,
    )

    first = reconcile_effect_unknown(memory_store, now=time.time(), grace_s=300.0)
    second = reconcile_effect_unknown(memory_store, now=time.time(), grace_s=300.0)

    assert first.marked == [msg.message_id]
    assert second.scanned == 0
    assert second.marked == []
    reason = memory_store.get_message(msg.message_id).effect_unknown_reason
    assert reason is not None
    assert memory_store.list_effect_unknown_candidates() == []
    assert sum(
        event.event_type == EVENT_TYPE
        for event in memory_store.list_events(msg.root_session_id)
    ) == 1


@pytest.mark.asyncio
async def test_dispatcher_reconcile_once_is_invokable(
    memory_store: CoordinationStore,
) -> None:
    msg = _active_message(memory_store)
    memory_store.record_delivery_attempt(
        _old_attempt(msg, state="confirmed", age_s=301.0),
        mark_outbox_done=True,
    )

    dispatcher = CoordinationDispatcher(memory_store)
    report = await dispatcher.reconcile_once(grace_s=300.0)

    assert report.marked == [msg.message_id]
