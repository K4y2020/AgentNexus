"""Persistence contract for durable teammate bots."""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod

from agentnexus.entities import Bot, BotComputerBinding, BotProjectBinding, ComputerExecutionLease


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

    @abstractmethod
    def bind_project(
        self,
        *,
        bot_id: str,
        project_id: str,
        checkout_root: str,
        default_branch: str = "main",
    ) -> BotProjectBinding: ...

    @abstractmethod
    def get_project_binding(
        self,
        *,
        bot_id: str,
        project_id: str,
    ) -> BotProjectBinding | None: ...

    @abstractmethod
    def list_project_bindings(self, bot_id: str) -> builtins.list[BotProjectBinding]: ...

    @abstractmethod
    def unbind_project(self, *, bot_id: str, project_id: str) -> bool: ...

    @abstractmethod
    def acquire_execution_lease(
        self,
        *,
        computer_id: str,
        bot_id: str,
        session_id: str,
        run_id: str,
        path: str,
        ttl_seconds: float = 120.0,
    ) -> ComputerExecutionLease: ...

    @abstractmethod
    def release_execution_lease(self, run_id: str) -> bool: ...

    @abstractmethod
    def get_active_lease(
        self,
        path: str,
        *,
        now: float | None = None,
    ) -> ComputerExecutionLease | None: ...
