"""Local Host Computer Provider for Phase 2."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from agentnexus.computers.provider import (
    ComputerCapabilities,
    ComputerProvider,
    ResolvedRunWorkspace,
)
from agentnexus.errors import AgentNexusError, ErrorCode
from agentnexus.stores.bot_store import BotStore

_logger = logging.getLogger(__name__)


class LocalHostComputerProvider(ComputerProvider):
    """Execution provider running directly on the local machine host."""

    def __init__(self, bot_store: BotStore) -> None:
        self.bot_store = bot_store

    def capabilities(self) -> ComputerCapabilities:
        return ComputerCapabilities(
            provider_kind="local_host",
            supports_git_worktrees=True,
            supports_leases=True,
            supports_containers=False,
            supports_gui=False,
        )

    def resolve_run_workspace(
        self,
        *,
        bot_id: str,
        session_id: str,
        run_id: str,
        project_id: str | None = None,
        access_mode: str = "write",
        is_git: bool | None = None,
    ) -> ResolvedRunWorkspace:
        binding = self.bot_store.get_binding(bot_id)
        if binding is None:
            raise AgentNexusError(
                f"Bot '{bot_id}' has no computer binding or home directory",
                code=ErrorCode.NOT_FOUND,
            )

        home = Path(binding.home_path)

        # No Project: isolate topics while keeping repeated runs in the same directory.
        if not project_id:
            from agentnexus.bot_workspace import ensure_bot_task_workspace

            task = ensure_bot_task_workspace(binding.home_path, session_id)
            return ResolvedRunWorkspace(path=task, is_worktree=False)

        # 2. Project provided: look up BotProjectBinding
        p_binding = self.bot_store.get_project_binding(bot_id=bot_id, project_id=project_id)
        if p_binding is None:
            raise AgentNexusError(
                f"Bot '{bot_id}' is not bound to project '{project_id}'",
                code=ErrorCode.NOT_FOUND,
            )

        checkout_root = Path(p_binding.checkout_root)
        if not checkout_root.exists():
            raise AgentNexusError(
                f"Project checkout root does not exist on host: {checkout_root}",
                code=ErrorCode.NOT_FOUND,
            )

        repo_is_git = (checkout_root / ".git").exists() if is_git is None else is_git

        # 3. Read-only mode: share the checkout root safely
        if access_mode == "read":
            return ResolvedRunWorkspace(
                path=str(checkout_root),
                is_worktree=False,
                project_id=project_id,
            )

        # 4. Write mode
        if repo_is_git:
            # Git write run: automatically allocate an isolated worktree
            # to guarantee index.lock conflict count = 0 across concurrent runs.
            import re

            worktree_dir = home / "worktrees" / project_id / run_id
            worktree_dir.parent.mkdir(parents=True, exist_ok=True)
            sanitized_run = re.sub(r"[^a-zA-Z0-9_.-]", "_", run_id)
            branch_name = f"bot/{bot_id[:8]}/{sanitized_run}"

            from agentnexus.host.git_worktree import WorktreeError, create_worktree

            try:
                created_info = create_worktree(
                    repo_path=str(checkout_root),
                    branch_name=branch_name,
                    base_branch=p_binding.default_branch,
                )
                return ResolvedRunWorkspace(
                    path=created_info.worktree_path,
                    is_worktree=True,
                    worktree_path=created_info.worktree_path,
                    branch=created_info.branch,
                    run_id=run_id,
                    project_id=project_id,
                )
            except WorktreeError as exc:
                _logger.error(
                    "Worktree creation failed for bot %s on project %s: %s",
                    bot_id,
                    project_id,
                    exc,
                )
                raise AgentNexusError(
                    f"Failed to allocate worktree for run '{run_id}': {exc.message}. "
                    "Write run rejected to protect main checkout.",
                    code=ErrorCode.CONFLICT,
                ) from exc
        else:
            # Non-Git write run: acquire an exclusive execution lease
            lease = self.bot_store.acquire_execution_lease(
                computer_id="local_host",
                bot_id=bot_id,
                session_id=session_id,
                run_id=run_id,
                path=str(checkout_root),
                ttl_seconds=300.0,
            )
            return ResolvedRunWorkspace(
                path=str(checkout_root),
                is_worktree=False,
                lease_id=lease.id,
                run_id=run_id,
                project_id=project_id,
            )

    def cleanup_run_workspace(
        self,
        *,
        bot_id: str,
        workspace: ResolvedRunWorkspace,
    ) -> None:
        binding = self.bot_store.get_binding(bot_id)
        home = Path(binding.home_path) if binding else None

        if workspace.is_worktree and workspace.worktree_path:
            wt_path = Path(workspace.worktree_path)
            if wt_path.exists():
                try:
                    from agentnexus.host.git_worktree import remove_worktree

                    remove_worktree(worktree_path=str(wt_path))
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "Failed to remove worktree cleanly: %s; moving to quarantine", exc
                    )
                    if home is not None:
                        quarantine = home / "worktrees" / "quarantine" / wt_path.name
                        quarantine.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.move(str(wt_path), str(quarantine))
                        except Exception as q_exc:  # noqa: BLE001
                            _logger.error("Failed to quarantine worktree: %s", q_exc)

        if workspace.run_id:
            try:
                self.bot_store.release_execution_lease(workspace.run_id)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("Lease release notice: %s", exc)
