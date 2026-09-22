"""Shared HTTP transport for TypeSafe System One (JEV) calls.

The SDK builds its HTTP client from the ambient environment. On a machine with
a loopback proxy configured using an ``https://`` scheme — which is what a
Windows WinINET proxy produces, and what people commonly export in
``https_proxy`` — httpx then attempts a TLS handshake *with the plaintext
proxy itself*. The proxy answers with an immediate EOF, so every JEV call dies
with a bare SSL error before a request is ever sent:

    TypeSafeAPIConnectionError: EOF occurred in violation of protocol

Normalizing the scheme to ``http://`` for loopback proxies restores the
intended CONNECT tunnel. A direct connection is also offered, because a broken
proxy should not mask a reachable API.
"""

from __future__ import annotations

import os
import re
from itertools import islice

_LOOPBACK_HTTP_SCHEME = re.compile(
    r"^https://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/?$", re.IGNORECASE
)
_PROXY_ENV_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


def effective_proxy() -> str | None:
    """Return the configured proxy with a corrected scheme, if one is set.

    Reads the environment first, then falls back to the platform proxy settings
    that ``urllib.request.getproxies()`` exposes (on Windows these come from the
    WinINET registry).
    """
    candidates = [os.environ.get(var, "") for var in _PROXY_ENV_VARS]
    try:
        import urllib.request

        detected = urllib.request.getproxies()
    except Exception:
        detected = {}
    candidates.extend(detected.get(scheme, "") for scheme in ("https", "http"))

    for value in candidates:
        value = (value or "").strip()
        if not value:
            continue
        if _LOOPBACK_HTTP_SCHEME.match(value):
            return "http://" + value[len("https://") :]
        return value
    return None


def client_kwargs(*, timeout: float = 30) -> list[dict]:
    """Return httpx client kwargs, most likely to work first."""
    proxy = effective_proxy()
    attempts: list[dict] = []
    if proxy:
        attempts.append({"timeout": timeout, "proxy": proxy, "trust_env": False})
    attempts.append({"timeout": timeout, "trust_env": False})
    return attempts


def open_http_client(*, timeout: float = 30):
    """Build an httpx client that tolerates a misconfigured loopback proxy."""
    import httpx2

    kwargs = client_kwargs(timeout=timeout)[0]
    return httpx2.Client(**kwargs)


def open_client(api_key: str, *, timeout: float = 30):
    """Open a TypeSafeClient that is resilient to a misconfigured proxy.

    Returns the first client that constructs successfully. The caller owns it
    and should use it as a context manager.
    """
    from typesafe_sdk import TypeSafeClient

    return TypeSafeClient(api_key=api_key, http_client=open_http_client(timeout=timeout))


def is_transport_error(exc: BaseException) -> bool:
    """True when a failure is a connection problem rather than a judgment."""
    names = {type(exc).__name__}
    for base in type(exc).__mro__:
        names.add(base.__name__)
    if names & {
        "APIConnectionError",
        "ConnectError",
        "ConnectTimeout",
        "ProxyError",
        "TransportError",
        "TimeoutException",
    }:
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))


__all__ = [
    "client_kwargs",
    "effective_proxy",
    "is_transport_error",
    "open_client",
]
