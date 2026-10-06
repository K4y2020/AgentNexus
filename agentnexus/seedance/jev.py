"""TypeSafe System One (JEV) calls made by the runner for Cine.

The film-analysis pipeline has its own synchronous client in
``pipeline/jev_transport.py``; this one is async and needs no TypeSafe SDK.
Both read the key and the proxy the same way.
"""

from __future__ import annotations

import os
import re
import urllib.request
from collections.abc import Iterator
from typing import Any

import httpx

from agentnexus.seedance.env_keys import read_key

SYSTEM_ONE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"

_LOOPBACK_HTTPS = re.compile(r"^https://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/?$", re.IGNORECASE)
_PROXY_ENV_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


def effective_proxy() -> str | None:
    """The configured proxy, with an ``https://`` loopback scheme corrected to ``http://``.

    A loopback proxy configured with an ``https://`` scheme (common on Windows,
    where it comes from the WinINET registry) makes httpx attempt TLS with the
    plaintext proxy itself, which answers with an immediate EOF.
    """
    candidates = [os.environ.get(var, "") for var in _PROXY_ENV_VARS]
    try:
        detected = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 - proxy discovery is optional
        detected = {}
    candidates.extend(detected.get(scheme, "") for scheme in ("https", "http"))
    for value in candidates:
        value = (value or "").strip()
        if not value:
            continue
        if _LOOPBACK_HTTPS.match(value):
            return "http://" + value[len("https://") :]
        return value
    return None


def _client_attempts() -> Iterator[dict[str, Any]]:
    """httpx client kwargs, best first; a broken proxy must not mask a reachable API."""
    proxy = effective_proxy()
    if proxy:
        yield {"timeout": 30, "proxy": proxy, "trust_env": False}
    yield {"timeout": 30, "trust_env": False}


def _wire_questions(questions: dict[str, Any]) -> dict[str, dict[str, Any]] | str:
    """The API's question format, or an error message for the first invalid spec."""
    wire: dict[str, dict[str, Any]] = {}
    for qid, spec in questions.items():
        if not isinstance(spec, dict):
            return f"question {qid!r} must be an object"
        qtype = str(spec.get("type", "")).lower()
        instructions = str(spec.get("instructions", "") or "")
        criteria = spec.get("criteria")
        if qtype == "choice":
            wire[str(qid)] = {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria or {},
            }
        elif qtype == "noul":
            wire[str(qid)] = {"type": "noul", "instructions": instructions}
        elif qtype == "score":
            wire[str(qid)] = {
                "type": "score",
                "instructions": instructions,
                "criteria": criteria if isinstance(criteria, list) else [],
            }
        else:
            return f"question {qid} has unknown type {qtype!r}"
    return wire


def _flatten(answers: dict[str, Any]) -> dict[str, Any]:
    """Answers in a flat, agent-friendly shape."""
    out: dict[str, Any] = {}
    for qid, answer in answers.items():
        atype = str(answer.get("type", ""))
        if atype == "choice":
            out[qid] = {
                "type": "choice",
                "choice": answer.get("choice"),
                "probabilities": answer.get("probabilities"),
                "confidence": answer.get("confidence"),
            }
        elif atype == "noul":
            out[qid] = {"type": "noul", "noul": answer.get("noul")}
        elif atype == "score":
            out[qid] = {
                "type": "score",
                "score": answer.get("score"),
                "probabilities": answer.get("probabilities"),
                "confidence": answer.get("confidence"),
            }
        else:
            out[qid] = answer
    return out


async def ask_system_one(
    state: Any, questions: Any, *, model: str = DEFAULT_MODEL
) -> dict[str, Any]:
    """Ask a batch of typed questions about ``state``.

    Returns ``{"model", "answers"}``, or ``{"error", ...}``; HTTP failures and an
    incomplete answer set are reported, never raised.
    """
    if not isinstance(state, (dict, list, str)):
        return {"error": "state must be an object, array, or string"}
    if not isinstance(questions, dict) or not questions:
        return {"error": "questions must be a non-empty object"}
    wire = _wire_questions(questions)
    if isinstance(wire, str):
        return {"error": wire}
    api_key = read_key(API_KEY_ENV)
    if not api_key:
        return {"error": f"{API_KEY_ENV} is not configured on this host"}

    payload = {"state": state, "model": model, "questions": wire}
    body: Any = None
    try:
        for client_kwargs in _client_attempts():
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.post(
                        SYSTEM_ONE_URL,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    body = response.json()
                break
            except (httpx.ConnectError, httpx.ProxyError, httpx.ConnectTimeout):
                # A misconfigured loopback proxy often breaks only one of the
                # two transports; the direct connection may still work.
                if not client_kwargs.get("proxy"):
                    raise
    except httpx.HTTPStatusError as exc:
        return {"error": f"typesafe api {exc.response.status_code}: {exc.response.text[:200]}"}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"error": f"jev call failed: {exc}"}

    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict) or set(answers) != set(wire):
        return {
            "error": "typesafe api returned an incomplete answer set",
            "expected_questions": sorted(wire),
            "received_answers": sorted(answers) if isinstance(answers, dict) else [],
        }
    return {"model": body.get("model", model), "answers": _flatten(answers)}
