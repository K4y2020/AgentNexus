"""Gateway management route tests (``/v1/gateways``)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.model_fallbacks import ANTHROPIC_GATEWAY_DEFAULT_ALIASES
from agentnexus.server.routes import gateways


def _save_gateway(
    monkeypatch: pytest.MonkeyPatch, existing: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """POST *payload* against an in-memory config and return the saved providers."""
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(gateways, "_load_global_config", lambda: {"providers": existing})
    monkeypatch.setattr(gateways, "_save_global_config", saved.append)
    app = FastAPI()
    app.include_router(gateways.create_gateways_router(), prefix="/v1")
    with TestClient(app) as client:
        resp = client.post("/v1/gateways", json=payload)
    assert resp.status_code == 200, resp.text
    return saved[-1]["providers"]


def test_new_anthropic_gateway_gets_default_claude_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new Anthropic gateway with only a default model gets the alias defaults."""
    providers = _save_gateway(
        monkeypatch,
        {},
        {
            "id": "proxy",
            "family": "anthropic",
            "base_url": "https://gw.example",
            "default_model": "proxy-default",
        },
    )

    models = providers["proxy"]["anthropic"]["models"]
    assert models["default"] == "proxy-default"
    assert {alias: models[alias] for alias, _ in ANTHROPIC_GATEWAY_DEFAULT_ALIASES} == dict(
        ANTHROPIC_GATEWAY_DEFAULT_ALIASES
    )


def test_gateway_aliases_skip_declared_mappings_and_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alias defaults never touch a mapped Anthropic gateway or an OpenAI one."""
    declared = {"default": "proxy-default", "sonnet": "proxy-sonnet"}
    providers = _save_gateway(
        monkeypatch,
        {
            "proxy": {
                "kind": "gateway",
                "anthropic": {"base_url": "https://gw", "models": declared},
            }
        },
        {"id": "proxy", "family": "anthropic", "base_url": "https://gw"},
    )
    assert providers["proxy"]["anthropic"]["models"] == declared

    providers = _save_gateway(
        monkeypatch,
        {},
        {"id": "oa", "family": "openai", "base_url": "https://gw", "default_model": "m"},
    )
    assert providers["oa"]["openai"]["models"] == {"default": "m"}
