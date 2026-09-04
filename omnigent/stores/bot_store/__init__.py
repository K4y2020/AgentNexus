"""Persistence contract for durable teammate bots."""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities import Bot, BotComputerBinding


class BotStore(ABC):
    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location

    @abstractmethod
    def ensure_for_agent(
        self,
        *,
        owner_id: str,
        agent_id: str,
        name: str,
        description: str | None,
    ) -> Bot: ...

    @abstractmethod
    def get(self, bot_id: str, *, owner_id: str | None = None) -> Bot | None: ...

    @abstractmethod
    def get_by_agent(self, agent_id: str, *, owner_id: str) -> Bot | None: ...

    @abstractmethod
    def list(self, *, owner_id: str, include_archived: bool = False) -> list[Bot]: ...

    @abstractmethod
    def update_settings(
        self,
        bot_id: str,
        *,
        owner_id: str,
        name: str,
        description: str | None,
        status: str,
        default_model: str | None,
        behavior_mode: str,
    ) -> Bot | None: ...

    @abstractmethod
    def ensure_binding(
        self,
        *,
        bot_id: str,
        host_id: str | None,
        home_path: str,
    ) -> BotComputerBinding: ...

    @abstractmethod
    def get_binding(self, bot_id: str) -> BotComputerBinding | None: ...

    @abstractmethod
    def update_binding(
        self,
        bot_id: str,
        *,
        host_id: str | None,
        home_path: str,
    ) -> BotComputerBinding | None: ...
