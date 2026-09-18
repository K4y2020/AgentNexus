"""Usage telemetry for the AgentNexus server.

This package provides fire-and-forget product analytics.  Import the
top-level helpers rather than reaching into submodules directly:

    from agentnexus.telemetry import emit, is_disabled

The :func:`emit` function accepts any event dataclass defined in
:mod:`omnigent.telemetry.events`.
"""

from __future__ import annotations

from agentnexus.telemetry.client import emit, init_client, is_disabled

__all__ = [
    "emit",
    "init_client",
    "is_disabled",
]
