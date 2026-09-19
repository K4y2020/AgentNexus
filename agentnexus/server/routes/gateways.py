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

from agentnexus.cli import _load_global_config, _save_global_config
from agentnexus.server.auth import AuthProvider
from agentnexus.server.routes._auth_helpers import require_user

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
            found_families = [f for f in ("anthropic", "openai") if f in p and isinstance(p[f], dict)]
            if not found_families:
                found_families = ["other"]
            for family in found_families:
                sub = p.get(family, {}) if isinstance(p.get(family), dict) else {}
                base_url = str(sub.get("base_url", "") or "")
                raw_key = sub.get("api_key") or sub.get("api_key_ref") or ""
                models_dict = sub.get("models", {}) if isinstance(sub.get("models"), dict) else {}
                default_model = models_dict.get("default", "")

                card_id = f"{pid}-{family}" if len(found_families) > 1 else pid
                gateways.append({
                    "id": card_id,
                    "name": f"{pid} ({family})" if len(found_families) > 1 else pid,
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
        """Create or update a gateway entry in ~/.agentnexus/config.yaml."""
        require_user(request, auth_provider)
        cfg = _load_global_config()
        providers = cfg.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}

        norm_id = payload.id.strip().replace(" ", "-")
        for suffix in ("-anthropic", "-openai"):
            if norm_id.endswith(suffix):
                norm_id = norm_id[:-len(suffix)]
                break
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
            models.setdefault("sonnet", "claude-sonnet-5")
            models.setdefault("opus", "claude-opus-5")
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

        # Preserve existing sibling families so updating openai doesn't wipe anthropic!
        new_entry: dict[str, Any] = dict(existing_entry)
        new_entry["kind"] = payload.kind or existing_entry.get("kind", "gateway")
        new_entry["default"] = payload.is_default
        new_entry[payload.family] = sub_config

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
        if not isinstance(providers, dict):
            providers = {}

        target_id = gateway_id
        for suffix in ("-anthropic", "-openai"):
            if target_id.endswith(suffix) and target_id not in providers:
                stripped = target_id[:-len(suffix)]
                if stripped in providers:
                    target_id = stripped
                    break

        if target_id not in providers:
            raise HTTPException(status_code=404, detail=f"Gateway {gateway_id} not found")

        del providers[target_id]
        _save_global_config({"providers": providers})
        return {"status": "ok", "deleted": target_id}

    @router.post("/gateways/{gateway_id}/set-default")
    async def set_default_gateway(gateway_id: str, request: Request) -> dict[str, Any]:
        """Set a gateway as the default provider for its family."""
        require_user(request, auth_provider)
        cfg = _load_global_config()
        providers = cfg.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}

        target_id = gateway_id
        forced_family = None
        for suffix in ("-anthropic", "-openai"):
            if target_id.endswith(suffix) and target_id not in providers:
                forced_family = suffix[1:]
                stripped = target_id[:-len(suffix)]
                if stripped in providers:
                    target_id = stripped
                    break

        if target_id not in providers:
            raise HTTPException(status_code=404, detail=f"Gateway {gateway_id} not found")

        target = providers[target_id]
        if not isinstance(target, dict):
            raise HTTPException(status_code=400, detail="Invalid gateway entry")

        target_fam = forced_family or ("anthropic" if "anthropic" in target else ("openai" if "openai" in target else None))
        if not target_fam:
            raise HTTPException(status_code=400, detail="Gateway has no anthropic or openai family")

        for other_id, other_p in providers.items():
            if isinstance(other_p, dict) and target_fam in other_p:
                other_p["default"] = (other_id == target_id)

        _save_global_config({"providers": providers})
        return {"status": "ok", "default_gateway": target_id, "family": target_fam}

    @router.post("/gateways/test")
    async def test_gateway(payload: GatewayTestPayload, request: Request) -> dict[str, Any]:
        """Test HTTP connectivity and retrieve available models from a gateway endpoint."""
        require_user(request, auth_provider)
        base_url = payload.base_url.strip().rstrip("/")
        if not base_url:
            raise HTTPException(status_code=400, detail="Base URL is required")

        base_clean = base_url[:-3] if base_url.endswith("/v1") else base_url

        key = payload.api_key.strip() if payload.api_key else None
        if not key:
            cfg = _load_global_config()
            providers = cfg.get("providers", {})
            if isinstance(providers, dict):
                for p in providers.values():
                    if isinstance(p, dict):
                        for fam in ("anthropic", "openai", payload.family):
                            sub = p.get(fam, {})
                            if isinstance(sub, dict):
                                b = str(sub.get("base_url", "")).rstrip("/")
                                if b and (b == base_url or b == base_clean or b == f"{base_clean}/v1"):
                                    k = sub.get("api_key")
                                    if isinstance(k, str) and k:
                                        key = k
                                        break
                    if key:
                        break

        headers: dict[str, str] = {}
        if key:
            if payload.family == "anthropic":
                headers["x-api-key"] = key
                headers["anthropic-version"] = "2023-06-01"
            headers["Authorization"] = f"Bearer {key}"

        test_urls = [
            f"{base_clean}/v1/models",
            f"{base_clean}/models",
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
                    elif resp.status_code in (401, 403):
                        last_error = f"HTTP {resp.status_code}: Authentication failed. Please check API Key."
                    elif not last_error or last_error.startswith("HTTP 404") or last_error == "Unknown error":
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