"""Gateway and Model Provider management routes (/v1/gateways).

Allows viewing, adding, updating, testing, and switching default API gateways
directly from the Web UI.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from omnigent.cli import _load_global_config, _save_global_config
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user

_logger = logging.getLogger(__name__)


class GatewayPayload(BaseModel):
    id: str = Field(..., min_length=1, description="Unique provider ID")
    kind: str = Field(default="gateway", description="Provider kind: gateway, local, etc.")
    family: str = Field(default="anthropic", description="Protocol family: anthropic or openai")
    base_url: str = Field(..., min_length=1, description="Base URL of the gateway")
    api_key: str | None = Field(default=None, description="API Key or bearer token")
    is_default: bool = Field(default=False, description="Whether this is the default provider for its family")
    default_model: str | None = Field(default=None, description="Default model ID for this gateway")
    wire_api: str | None = Field(default=None, description="Wire API: responses or chat (OpenAI only)")


class GatewayTestPayload(BaseModel):
    base_url: str = Field(..., min_length=1)
    family: str = Field(default="anthropic")
    api_key: str | None = None


def _mask_api_key(key: str | None) -> str:
    if not key:
        return ""
    if key.startswith(("env:", "keychain:")):
        return key
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def create_gateways_router(*, auth_provider: AuthProvider | None = None) -> APIRouter:
    """Build the router for /v1/gateways."""
    router = APIRouter()

    @router.get("/gateways")
    async def list_gateways(request: Request) -> dict[str, Any]:
        """List all configured model gateways and providers."""
        require_user(request, auth_provider)
        cfg = _load_global_config()
        providers = cfg.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}

        gateways = []
        for pid, p in providers.items():
            if not isinstance(p, dict):
                continue
            family = "anthropic" if "anthropic" in p else ("openai" if "openai" in p else "other")
            sub = p.get(family, {}) if isinstance(p.get(family), dict) else {}
            base_url = str(sub.get("base_url", "") or "")
            raw_key = sub.get("api_key") or sub.get("api_key_ref") or ""
            models_dict = sub.get("models", {}) if isinstance(sub.get("models"), dict) else {}
            default_model = models_dict.get("default", "")

            gateways.append({
                "id": pid,
                "name": pid,
                "kind": p.get("kind", "gateway"),
                "family": family,
                "base_url": base_url,
                "has_api_key": bool(raw_key),
                "api_key_masked": _mask_api_key(str(raw_key) if raw_key else None),
                "is_default": bool(p.get("default")),
                "default_model": default_model,
                "wire_api": sub.get("wire_api", ""),
            })

        return {"gateways": gateways}

    @router.post("/gateways")
    async def create_or_update_gateway(payload: GatewayPayload, request: Request) -> dict[str, Any]:
        """Create or update a gateway entry in ~/.omnigent/config.yaml."""
        require_user(request, auth_provider)
        cfg = _load_global_config()
        providers = cfg.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}

        norm_id = payload.id.strip().replace(" ", "-")
        base_url = payload.base_url.strip().rstrip("/")
        clean_key = payload.api_key.strip() if payload.api_key else None

        # If marking as default, clear default on sibling providers of the same family
        if payload.is_default:
            for other_id, other_p in providers.items():
                if other_id != norm_id and isinstance(other_p, dict) and payload.family in other_p:
                    other_p["default"] = False

        existing_entry = providers.get(norm_id, {})
        if not isinstance(existing_entry, dict):
            existing_entry = {}

        existing_family_sub = existing_entry.get(payload.family, {})
        if not isinstance(existing_family_sub, dict):
            existing_family_sub = {}

        # Preserve or update models dict
        models = existing_family_sub.get("models", {})
        if not isinstance(models, dict):
            models = {}
        if payload.default_model:
            models["default"] = payload.default_model
        if payload.family == "anthropic" and len(models) <= 1:
            models.setdefault("sonnet", "claude-sonnet-4-6")
            models.setdefault("opus", "claude-opus-4-8")
            models.setdefault("haiku", "claude-haiku-4-5")
            models.setdefault("fable", "claude-fable-5")
            models.setdefault("claude-opus-4-8", "claude-opus-4-6-thinking")
            models.setdefault("claude-haiku-4-5", "gpt-5.6-terra")
            models.setdefault("claude-fable-5", "gpt-5.6-sol")

        sub_config: dict[str, Any] = {
            "base_url": base_url,
            "models": models,
        }
        if clean_key:
            if clean_key.startswith(("env:", "keychain:")):
                sub_config["api_key_ref"] = clean_key
            else:
                sub_config["api_key"] = clean_key
        elif "api_key" in existing_family_sub:
            sub_config["api_key"] = existing_family_sub["api_key"]
        elif "api_key_ref" in existing_family_sub:
            sub_config["api_key_ref"] = existing_family_sub["api_key_ref"]

        if payload.family == "openai" and payload.wire_api:
            sub_config["wire_api"] = payload.wire_api

        new_entry: dict[str, Any] = {
            "kind": payload.kind or "gateway",
            "default": payload.is_default,
            payload.family: sub_config,
        }

        providers[norm_id] = new_entry
        _save_global_config({"providers": providers})

        return {
            "status": "ok",
            "id": norm_id,
            "message": f"Gateway {norm_id} saved successfully.",
        }

    @router.delete("/gateways/{gateway_id}")
    async def delete_gateway(gateway_id: str, request: Request) -> dict[str, Any]:
        """Delete a gateway by its identifier."""
        require_user(request, auth_provider)
        cfg = _load_global_config()
        providers = cfg.get("providers", {})
        if not isinstance(providers, dict) or gateway_id not in providers:
            raise HTTPException(status_code=404, detail=f"Gateway {gateway_id} not found")

        del providers[gateway_id]
        _save_global_config({"providers": providers})
        return {"status": "ok", "deleted": gateway_id}

    @router.post("/gateways/{gateway_id}/set-default")
    async def set_default_gateway(gateway_id: str, request: Request) -> dict[str, Any]:
        """Set a gateway as the default provider for its family."""
        require_user(request, auth_provider)
        cfg = _load_global_config()
        providers = cfg.get("providers", {})
        if not isinstance(providers, dict) or gateway_id not in providers:
            raise HTTPException(status_code=404, detail=f"Gateway {gateway_id} not found")

        target = providers[gateway_id]
        if not isinstance(target, dict):
            raise HTTPException(status_code=400, detail="Invalid gateway entry")

        target_fam = "anthropic" if "anthropic" in target else ("openai" if "openai" in target else None)
        if not target_fam:
            raise HTTPException(status_code=400, detail="Gateway has no anthropic or openai family")

        for other_id, other_p in providers.items():
            if isinstance(other_p, dict) and target_fam in other_p:
                other_p["default"] = (other_id == gateway_id)

        _save_global_config({"providers": providers})
        return {"status": "ok", "default_gateway": gateway_id, "family": target_fam}

    @router.post("/gateways/test")
    async def test_gateway(payload: GatewayTestPayload, request: Request) -> dict[str, Any]:
        """Test HTTP connectivity and retrieve available models from a gateway endpoint."""
        require_user(request, auth_provider)
        base_url = payload.base_url.strip().rstrip("/")
        if not base_url:
            raise HTTPException(status_code=400, detail="Base URL is required")

        headers: dict[str, str] = {}
        if payload.api_key:
            key = payload.api_key.strip()
            if payload.family == "anthropic":
                headers["x-api-key"] = key
                headers["anthropic-version"] = "2023-06-01"
            headers["Authorization"] = f"Bearer {key}"

        test_urls = [
            f"{base_url}/v1/models",
            f"{base_url}/models",
            base_url,
        ]

        t0 = time.perf_counter()
        last_error = "Unknown error"
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            for url in test_urls:
                try:
                    resp = await client.get(url, headers=headers)
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            model_list = []
                            if isinstance(data, dict):
                                items = data.get("data") or data.get("models") or []
                                if isinstance(items, list):
                                    for item in items:
                                        if isinstance(item, dict) and "id" in item:
                                            model_list.append(str(item["id"]))
                                        elif isinstance(item, str):
                                            model_list.append(item)
                            return {
                                "status": "ok",
                                "latency_ms": latency_ms,
                                "status_code": 200,
                                "models_count": len(model_list),
                                "models": model_list,
                                "message": f"Connected successfully! Found {len(model_list)} models ({latency_ms}ms).",
                            }
                        except Exception:
                            return {
                                "status": "ok",
                                "latency_ms": latency_ms,
                                "status_code": 200,
                                "models_count": 0,
                                "models": [],
                                "message": f"Connected successfully (HTTP 200, {latency_ms}ms).",
                            }
                    else:
                        last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                except httpx.ConnectError:
                    last_error = f"Cannot connect to {base_url} (Connection refused)"
                except httpx.TimeoutException:
                    last_error = f"Timeout connecting to {base_url} (exceeded 6s)"
                except Exception as exc:
                    last_error = str(exc)

        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "status": "error",
            "latency_ms": latency_ms,
            "error": last_error,
            "message": f"Connection failed: {last_error}",
        }

    return router