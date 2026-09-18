"""Tests for the per-turn ``model_fact`` transcript item."""

from __future__ import annotations

from agentnexus.entities import ModelFactData, NewConversationItem, parse_item_data


def test_model_fact_round_trips_all_three_layers() -> None:
    """A fully-known fact chain survives parse and NewConversationItem validation."""
    data = parse_item_data(
        "model_fact",
        {
            "requested_model": "databricks-gpt-5-6-sol",
            "resolved_model": "databricks-gpt-5-6-sol",
            "upstream_model": "gpt-5-6",
            "harness": "codex",
            "status": "completed",
            "source": "runner_relay",
        },
    )
    assert isinstance(data, ModelFactData)
    assert data.requested_model == "databricks-gpt-5-6-sol"
    assert data.resolved_model == "databricks-gpt-5-6-sol"
    assert data.upstream_model == "gpt-5-6"
    assert data.requested_unknown_reason is None
    assert data.resolved_unknown_reason is None
    assert data.upstream_unknown_reason is None
    assert data.harness == "codex"
    assert data.status == "completed"
    assert data.source == "runner_relay"

    item = NewConversationItem(
        type="model_fact",
        response_id="resp_1",
        data=data,
    )
    assert item.data.requested_model == "databricks-gpt-5-6-sol"


def test_model_fact_unknown_layers_carry_reasons() -> None:
    """Unknown layers keep a reason so the UI never shows bare Unknown."""
    data = parse_item_data(
        "model_fact",
        {
            "requested_model": None,
            "requested_unknown_reason": "no_explicit_selection",
            "resolved_model": None,
            "resolved_unknown_reason": "harness_reported_no_model",
            "upstream_model": None,
            "upstream_unknown_reason": "gateway_model_unavailable",
            "status": "failed",
        },
    )
    assert data.requested_model is None
    assert data.requested_unknown_reason == "no_explicit_selection"
    assert data.resolved_unknown_reason == "harness_reported_no_model"
    assert data.upstream_unknown_reason == "gateway_model_unavailable"


def test_model_fact_api_dict_flattens_snake_case_fields() -> None:
    """to_api_dict exposes the fact chain fields for GET items consumers."""
    from agentnexus.entities import ConversationItem

    item = ConversationItem(
        id="item_1",
        type="model_fact",
        status="completed",
        response_id="resp_1",
        created_at=1,
        data=ModelFactData(
            requested_model="databricks-gpt-5-6-sol",
            resolved_model="databricks-gpt-5-6-sol",
            upstream_unknown_reason="gateway_model_unavailable",
        ),
    )
    dumped = item.to_api_dict()
    assert dumped["type"] == "model_fact"
    assert dumped["requested_model"] == "databricks-gpt-5-6-sol"
    assert dumped["resolved_model"] == "databricks-gpt-5-6-sol"
    assert dumped["upstream_unknown_reason"] == "gateway_model_unavailable"
