"""Agent-memory entities — persisted in the ``agent_memories`` table."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentMemory:
    """One durable memory owned by a teammate bot.

    :param id: UUID primary key (bare 32-char hex string).
    :param agent_id: The teammate agent that owns this memory.
    :param content: Free-text memory payload.
    :param source: Where the memory came from — ``"manual"`` for UI/API
        writes today; future session write-back can use its own source tag.
    :param workspace_id: Tenant partition key that owns this row.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last content edit, or
        ``None`` if never edited.
    """

    id: str
    agent_id: str
    content: str
    source: str = "manual"
    workspace_id: int = 0
    created_at: int = 0
    updated_at: int | None = None
