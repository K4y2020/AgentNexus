"""Shared error factories for route handlers.

Centralizes the construction of common ``AgentNexusError`` instances so
that the wire message and error code have a single source of truth
across all route modules.  Import the factory and raise it directly::

    from agentnexus.server.routes._errors import session_not_found

    raise session_not_found()
    # or, to preserve exception chaining:
    raise session_not_found() from exc
"""

from __future__ import annotations

from agentnexus.errors import AgentNexusError, ErrorCode

_SESSION_NOT_FOUND: str = "Session not found"


def session_not_found() -> AgentNexusError:
    """Build the canonical ``NOT_FOUND`` error for a vanished session.

    Every "the conversation row is gone" branch across the route modules
    raises the same message and :class:`ErrorCode.NOT_FOUND` code;
    centralizing the construction keeps the wire response identical
    across handlers.  Raise it directly with ``raise session_not_found()``,
    or ``raise session_not_found() from exc`` to preserve cause chaining.

    :returns: A fresh :class:`AgentNexusError` with message
        ``"Session not found"`` and code :attr:`ErrorCode.NOT_FOUND`.
    """
    return AgentNexusError(_SESSION_NOT_FOUND, code=ErrorCode.NOT_FOUND)
