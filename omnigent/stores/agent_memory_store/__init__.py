"""Agent-memory store — persists durable per-bot memories."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities import AgentMemory


class AgentMemoryStore(ABC):
    """Abstract base for teammate memory persistence."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def create(
        self,
        memory_id: str,
        agent_id: str,
        content: str,
        *,
        source: str = "manual",
    ) -> AgentMemory:
        """Insert a new memory row for a teammate."""
        ...

    @abstractmethod
    def list(self, agent_id: str, *, limit: int = 200) -> list[AgentMemory]:
        """List a teammate's memories, newest first."""
        ...

    @abstractmethod
    def update(
        self,
        agent_id: str,
        memory_id: str,
        content: str,
    ) -> AgentMemory | None:
        """Replace a memory's content, scoped to the owning agent."""
        ...

    @abstractmethod
    def delete(self, agent_id: str, memory_id: str) -> bool:
        """Delete a memory row, scoped to the owning agent."""
        ...
