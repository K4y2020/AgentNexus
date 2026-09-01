"""Targeted tests for the control plane's 11-layer error envelope."""

from omnigent.entities import ErrorData
from omnigent.error_layers import ERROR_LAYERS, classify_error_layer
from omnigent.server.schemas import ErrorDetail, RetryErrorDetail


def test_plan_error_layer_enum_has_exactly_11_layers() -> None:
    assert set(ERROR_LAYERS) == {
        "ui",
        "server",
        "host",
        "runner",
        "harness",
        "model",
        "provider",
        "tool",
        "workspace",
        "git",
        "policy",
    }


def test_classify_error_layer_maps_known_codes() -> None:
    cases = {
        "runner_disconnected": "runner",
        "harness_not_configured": "harness",
        "rate_limit_exceeded": "model",
        "provider_rate_limited": "provider",
        "workspace_missing": "workspace",
        "git_merge_conflict": "git",
        "policy_denied": "policy",
        "host_offline": "host",
        "session_not_found": "server",
        "tool_timeout": "tool",
    }
    for code, expected in cases.items():
        assert classify_error_layer(code) == expected


def test_classify_error_layer_falls_back_to_source_then_callers() -> None:
    assert classify_error_layer("mystery_code", source="llm") == "model"
    assert classify_error_layer("mystery_code", source="execution") == "runner"
    assert classify_error_layer(None, fallback="policy") == "policy"


def test_error_detail_autoclassifies_explicit_layer_is_preserved() -> None:
    detail = ErrorDetail(code="runner_disconnected", message="tunnel dropped")
    assert detail.layer == "runner"

    explicit = ErrorDetail(
        code="mystery_code",
        message="boom",
        layer="harness",
    )
    assert explicit.layer == "harness"


def test_retry_error_detail_keeps_plan_shaped_layers() -> None:
    detail = RetryErrorDetail(
        code="rate_limit_exceeded",
        message="All credentials cooling down",
        detail={"status_code": 429, "retry_after": 5},
    )
    assert detail.layer == "model"
    assert detail.detail == {"status_code": 429, "retry_after": 5}


def test_error_data_persists_structured_fields_and_layer() -> None:
    data = ErrorData(
        source="execution",
        code="model_change_not_applied",
        message="the terminal was not switched",
        title="Model change not applied",
        cause="runner rejected the forward",
        remediation="retry the model change",
        suggested_action="retry the model change",
        correlation_id="req_123",
        diagnostic_refs=["art_log_01"],
    )
    assert data.layer == "harness"
    assert data.title == "Model change not applied"
    assert data.correlation_id == "req_123"
    assert data.diagnostic_refs == ["art_log_01"]

    fallback = ErrorData(source="tool", code="unexpected_tool_failure", message="boom")
    assert fallback.layer == "tool"
