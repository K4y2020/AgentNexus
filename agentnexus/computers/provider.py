"""Computer provider protocol and capability contracts for Phase 2."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ResolvedRunWorkspace:
    """The resolved execution directory and isolation metadata for a Run."""

    path: str
    is_worktree: bool = False
    worktree_path: str | None = None
    branch: str | None = None
    lease_id: str | None = None
    run_id: str | None = None
    project_id: str | None = None


@dataclass
class ComputerCapabilities:
    """Operational capabilities exposed by a Computer Provider."""

    provider_kind: str
    supports_git_worktrees: bool = True
    supports_leases: bool = True
    supports_containers: bool = False
    supports_gui: bool = False


class ComputerProvider(ABC):
    """Abstract protocol for provisioning execution environments."""

    @abstractmethod
    def capabilities(self) -> ComputerCapabilities: ...

    @abstractmethod
    def resolve_run_workspace(
        self,
        *,
        bot_id: str,
        session_id: str,
        run_id: str,
        project_id: str | None = None,
        access_mode: str = "write",
        is_git: bool | None = None,
    ) -> ResolvedRunWorkspace: ...

    @abstractmethod
    def cleanup_run_workspace(
        self,
        *,
        bot_id: str,
        workspace: ResolvedRunWorkspace,
    ) -> None: ...
