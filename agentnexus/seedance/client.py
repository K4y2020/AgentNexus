"""Seedance V3 API client for AgentNexus / AgentNexus."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_SEEDANCE_BASE_URL = "http://127.0.0.1:8893"
DEFAULT_SEEDANCE_UI_BASE_URL = "http://127.0.0.1:5173"
DEFAULT_SEEDANCE_TIMEOUT_S = 900.0


class SeedanceError(Exception):
    """Base exception for Seedance API errors."""

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class SeedanceConnectionError(SeedanceError):
    """Raised when the Seedance service cannot be reached."""


class SeedanceAuthError(SeedanceError):
    """Raised when authentication fails (401 or 403)."""


class SeedanceNotFoundError(SeedanceError):
    """Raised when a project, session, or job is not found (404)."""


class SeedanceSessionBusyError(SeedanceError):
    """Raised when the Seedance agent session is currently busy (409)."""


class SeedanceTimeoutError(SeedanceError):
    """Raised when an agent request times out (504 or client timeout)."""


class SeedanceSecurityError(SeedanceError):
    """Raised when the configured base URL fails security constraints."""


def validate_seedance_base_url(url_str: str) -> str:
    """Ensure Seedance base URL is loopback or local IP or explicitly permitted."""
    parsed = urlparse(url_str)
    if parsed.scheme not in ("http", "https"):
        raise SeedanceSecurityError(f"Invalid Seedance URL scheme: {parsed.scheme}")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise SeedanceSecurityError("Seedance base URL must have a valid hostname")

    allowed_hosts = set(
        h.strip().lower()
        for h in os.environ.get("SEEDANCE_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    )
    allowed_hosts.update({"localhost", "127.0.0.1", "::1"})

    if hostname in allowed_hosts:
        return url_str.rstrip("/")

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_loopback:
            return url_str.rstrip("/")
        if (
            os.environ.get("SEEDANCE_ALLOW_PRIVATE_IP", "").strip().lower() in ("1", "true", "yes")
            and ip.is_private
        ):
            return url_str.rstrip("/")
    except ValueError:
        pass

    raise SeedanceSecurityError(
        f"Seedance base URL hostname {hostname!r} is not an allowed local/loopback address. "
        "For security, only loopback addresses (127.0.0.1, localhost) or explicit SEEDANCE_ALLOWED_HOSTS are permitted."
    )


def get_seedance_api_key() -> str | None:
    """Read SEEDANCE_API_KEY from environment or local seedance-v3 .env file fallback."""
    key = os.environ.get("SEEDANCE_API_KEY", "").strip()
    if key:
        return key
    candidate_paths = [
        r"U:\AI\seedance-v3\.env",
        os.path.expanduser(r"~\AI\seedance-v3\.env"),
    ]
    for p in candidate_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("SEEDANCE_API_KEY=") and not line.startswith("#"):
                            val = line.split("=", 1)[1].strip().strip("\"'")
                            if val:
                                return val
            except Exception:
                pass
    return None


class SeedanceClient:
    """Async client for Seedance V3 API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        raw_url = base_url or os.environ.get("SEEDANCE_BASE_URL", DEFAULT_SEEDANCE_BASE_URL)
        self.base_url = validate_seedance_base_url(raw_url)
        self.api_key = api_key if api_key is not None else get_seedance_api_key()
        self.timeout_s = (
            timeout_s
            if timeout_s is not None
            else float(os.environ.get("SEEDANCE_CONNECTOR_TIMEOUT_S", DEFAULT_SEEDANCE_TIMEOUT_S))
        )
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(timeout=self.timeout_s)

    async def close(self) -> None:
        if not self._external_client:
            await self._client.aclose()

    async def __aenter__(self) -> SeedanceClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _handle_response_error(self, resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise SeedanceAuthError(
                f"Authentication failed with Seedance API: {resp.status_code}",
                code="UNAUTHORIZED",
                status_code=resp.status_code,
            )
        if resp.status_code == 404:
            raise SeedanceNotFoundError(
                f"Seedance resource not found: {resp.request.url.path}",
                code="NOT_FOUND",
                status_code=resp.status_code,
            )
        if resp.status_code == 409:
            try:
                err_data = resp.json()
                code = err_data.get("code", "CONFLICT")
                msg = err_data.get("message", "Resource conflict")
            except Exception:
                code = "CONFLICT"
                msg = resp.text
            if "BUSY" in code or "busy" in msg.lower():
                raise SeedanceSessionBusyError(
                    f"Seedance agent session is busy: {msg}",
                    code=code,
                    status_code=409,
                )
            raise SeedanceError(f"Conflict: {msg}", code=code, status_code=409)
        if resp.status_code == 504:
            raise SeedanceTimeoutError(
                "Seedance agent request timed out (504)",
                code="AGENT_TIMEOUT",
                status_code=504,
            )
        if resp.status_code >= 400:
            try:
                err_data = resp.json()
                code = err_data.get("code")
                msg = err_data.get("message", resp.text)
            except Exception:
                code = None
                msg = resp.text
            raise SeedanceError(
                f"Seedance API error ({resp.status_code}): {msg}",
                code=code,
                status_code=resp.status_code,
            )

    async def healthz(self) -> dict[str, Any]:
        """Probe Seedance health without authentication."""
        url = f"{self.base_url}/healthz"
        try:
            resp = await self._client.get(url, timeout=5.0)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = {"status": "ok"}
                return {"status": "ok", "base_url": self.base_url, "details": data}
            self._handle_response_error(resp)
            return {"status": "error", "code": resp.status_code}
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(
                f"Failed to connect to Seedance at {self.base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise SeedanceTimeoutError(
                f"Seedance health check timed out at {self.base_url}: {exc}"
            ) from exc

    async def list_projects(self) -> list[dict[str, Any]]:
        """List existing projects in Seedance."""
        url = f"{self.base_url}/v3/projects"
        try:
            resp = await self._client.get(url, headers=self._headers())
            self._handle_response_error(resp)
            data = resp.json()
            return data.get("projects", [])
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def get_generation_models(self) -> dict[str, Any]:
        """Read the V3 generation catalog, not the chat-agent model list."""
        resp = await self._client.get(
            f"{self.base_url}/v3/generation-models", headers=self._headers()
        )
        self._handle_response_error(resp)
        return resp.json()

    async def get_local_image(self, storage_ref: str, max_bytes: int) -> bytes:
        import re

        if not re.fullmatch(r"local://[a-f0-9]{64}", storage_ref):
            raise SeedanceSecurityError("Invalid local image storage reference")
        chunks = []
        size = 0
        async with self._client.stream(
            "GET",
            f"{self.base_url}/v3/storage/{storage_ref[8:]}",
            headers=self._headers(),
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise SeedanceError(
                    "Image download failed",
                    code="CINE_IMAGE_DOWNLOAD_FAILED",
                    status_code=response.status_code,
                )
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise SeedanceError(
                        "Image exceeds the supported preview limit", code="CINE_IMAGE_TOO_LARGE"
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    async def get_node_references(self, project_id: str, node_id: str) -> list[dict[str, Any]]:
        """Use the same reference resolution as the V3 canvas UI."""
        resp = await self._client.get(
            f"{self.base_url}/v3/projects/{project_id}/nodes/{node_id}/references",
            headers=self._headers(),
        )
        self._handle_response_error(resp)
        references = resp.json().get("references")
        if not isinstance(references, list):
            raise SeedanceError("Malformed reference response from Seedance")
        return references

    async def validate_generation(
        self, project_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        resp = await self._client.post(
            f"{self.base_url}/v3/projects/{project_id}/generation/validate",
            json=command,
            headers=self._headers(),
        )
        self._handle_response_error(resp)
        return resp.json()

    async def optimize_prompt(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run V3's native Prompt Agent and full model contract validation."""
        resp = await self._client.post(
            f"{self.base_url}/v3/prompts/optimize",
            json=payload,
            headers=self._headers(),
        )
        self._handle_response_error(resp)
        return resp.json()

    async def create_project(self, name: str) -> dict[str, Any]:
        """Create a new project in Seedance."""
        url = f"{self.base_url}/v3/projects"
        try:
            resp = await self._client.post(url, json={"name": name}, headers=self._headers())
            self._handle_response_error(resp)
            data = resp.json()
            project = data.get("project")
            if not project or not isinstance(project, dict):
                raise SeedanceError("Malformed create_project response from Seedance")
            return project
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def get_snapshot(self, project_id: str) -> dict[str, Any]:
        """Read project snapshot containing canvas cards, nodes, and jobs."""
        url = f"{self.base_url}/v3/projects/{project_id}/snapshot"
        try:
            resp = await self._client.get(url, headers=self._headers())
            self._handle_response_error(resp)
            data = resp.json()
            snapshot = data.get("snapshot") or data
            if "nodeMedia" in data:
                snapshot = {**snapshot, "node_media": data["nodeMedia"]}
            return snapshot
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def get_graph(self, project_id: str) -> dict[str, Any]:
        """Read project graph representation."""
        url = f"{self.base_url}/v3/projects/{project_id}/graph"
        try:
            resp = await self._client.get(url, headers=self._headers())
            self._handle_response_error(resp)
            return resp.json()
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def create_agent_session(
        self,
        project_id: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Create an agent session attached to a project."""
        url = f"{self.base_url}/v3/projects/{project_id}/agent-sessions"
        body: dict[str, Any] = {}
        if model:
            body["model"] = model
        try:
            resp = await self._client.post(url, json=body, headers=self._headers())
            self._handle_response_error(resp)
            data = resp.json()
            session = data.get("session")
            if not session or not isinstance(session, dict):
                raise SeedanceError("Malformed create_agent_session response from Seedance")
            return session
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def send_agent_message(
        self,
        session_id: str,
        content: str,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Send an instruction message to the Seedance Agent Session."""
        url = f"{self.base_url}/v3/agent-sessions/{session_id}/messages"
        body: dict[str, Any] = {"content": content}
        if model:
            body["model"] = model
        req_timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            resp = await self._client.post(
                url, json=body, headers=self._headers(), timeout=req_timeout
            )
            self._handle_response_error(resp)
            return resp.json()
        except httpx.TimeoutException as exc:
            raise SeedanceTimeoutError(
                f"Agent message timed out after {req_timeout}s: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def submit_command(
        self,
        project_id: str,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit a domain command directly to the project command bus."""
        url = f"{self.base_url}/v3/projects/{project_id}/commands"
        try:
            resp = await self._client.post(url, json={"command": command}, headers=self._headers())
            self._handle_response_error(resp)
            return resp.json()
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Query generation job state."""
        url = f"{self.base_url}/v3/jobs/{job_id}"
        try:
            resp = await self._client.get(url, headers=self._headers())
            self._handle_response_error(resp)
            data = resp.json()
            return data.get("job") or data
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Cancel an in-flight generation job."""
        url = f"{self.base_url}/v3/jobs/{job_id}/cancel"
        try:
            resp = await self._client.post(url, headers=self._headers())
            self._handle_response_error(resp)
            return resp.json()
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc

    async def upload_asset(
        self,
        project_id: str,
        filename: str,
        content: bytes,
        mime_type: str = "image/png",
        node_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload a binary asset (e.g. keyframe image) to the project."""
        url = f"{self.base_url}/v3/projects/{project_id}/assets/upload"
        params = {}
        if node_id:
            params["nodeId"] = node_id
        headers = dict(self._headers())
        headers["Content-Type"] = "application/octet-stream"
        headers["X-Asset-Filename"] = filename
        headers["X-Asset-Mime-Type"] = mime_type

        try:
            resp = await self._client.post(url, params=params, content=content, headers=headers)
            self._handle_response_error(resp)
            return resp.json()
        except httpx.ConnectError as exc:
            raise SeedanceConnectionError(f"Failed to connect to Seedance: {exc}") from exc
