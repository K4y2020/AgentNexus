"""Workspace Lease and Git Coordination models and manager (WS-001, WS-002)."""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

LeaseMode = Literal["read", "write"]


@dataclass
class WorkspaceLease:
    """A durable lease protecting a checkout/worktree from conflicting concurrent writes."""

    lease_id: str = field(default_factory=lambda: f"lease_{uuid.uuid4().hex[:12]}")
    workspace_path: str = ""
    holder_session_id: str = ""
    mode: LeaseMode = "write"
    fencing_token: int = 1
    acquired_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600.0)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WorkspaceLeaseManager:
    """Manages concurrent read/write leases on local repositories and worktrees."""

    def __init__(self) -> None:
        self._leases: dict[str, WorkspaceLease] = {}
        self._fencing_counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(
        self,
        workspace_path: str | Path,
        holder_session_id: str,
        mode: LeaseMode = "write",
        duration_s: float = 600.0,
    ) -> WorkspaceLease:
        norm_path = os.path.normpath(str(workspace_path))
        with self._lock:
            existing = self._leases.get(norm_path)
            now = time.time()

            # Clean expired lease
            if existing and existing.is_expired:
                del self._leases[norm_path]
                existing = None

            if existing:
                # Same session renewing
                if existing.holder_session_id == holder_session_id:
                    existing.expires_at = now + duration_s
                    return existing
                if mode == "write" or existing.mode == "write":
                    raise RuntimeError(
                        f"Workspace {norm_path} is currently locked by session {existing.holder_session_id} "
                        f"until {time.ctime(existing.expires_at)} (mode={existing.mode})"
                    )

            counter = self._fencing_counters.get(norm_path, 0) + 1
            self._fencing_counters[norm_path] = counter

            lease = WorkspaceLease(
                workspace_path=norm_path,
                holder_session_id=holder_session_id,
                mode=mode,
                fencing_token=counter,
                acquired_at=now,
                expires_at=now + duration_s,
            )
            self._leases[norm_path] = lease
            return lease

    def release(self, workspace_path: str | Path, holder_session_id: str) -> bool:
        norm_path = os.path.normpath(str(workspace_path))
        with self._lock:
            existing = self._leases.get(norm_path)
            if existing and existing.holder_session_id == holder_session_id:
                del self._leases[norm_path]
                return True
            return False

    def get_lease(self, workspace_path: str | Path) -> WorkspaceLease | None:
        norm_path = os.path.normpath(str(workspace_path))
        with self._lock:
            lease = self._leases.get(norm_path)
            if lease and lease.is_expired:
                del self._leases[norm_path]
                return None
            return lease


class WorkspaceCoordinator:
    """Coordinates Git operations, isolated worktrees, and merge previews."""

    def __init__(self, lease_manager: WorkspaceLeaseManager | None = None) -> None:
        self.lease_manager = lease_manager or WorkspaceLeaseManager()

    def create_worktree(
        self, repo_path: str | Path, branch_name: str, base_branch: str = "main"
    ) -> Path:
        """Create a dedicated git worktree for an agent to work in isolation."""
        repo = Path(repo_path)
        worktree_dir = repo.parent / f"{repo.name}-worktrees" / branch_name
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        if not worktree_dir.exists():
            cmd = ["git", "-C", str(repo), "worktree", "add", "-B", branch_name, str(worktree_dir), base_branch]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                # Fallback to existing branch or simple worktree add
                cmd_fb = ["git", "-C", str(repo), "worktree", "add", str(worktree_dir), branch_name]
                res_fb = subprocess.run(cmd_fb, capture_output=True, text=True, check=False)
                if res_fb.returncode != 0:
                    raise RuntimeError(f"Failed to create worktree: {res.stderr or res_fb.stderr}")
        return worktree_dir

    def generate_merge_preview(
        self, repo_path: str | Path, source_branch: str, target_branch: str = "main"
    ) -> dict[str, object]:
        """Compute a clean diff preview between the source worktree branch and target branch."""
        repo = str(repo_path)
        diff_cmd = ["git", "-C", repo, "diff", f"{target_branch}...{source_branch}"]
        diff_res = subprocess.run(diff_cmd, capture_output=True, text=True, check=False)

        stat_cmd = ["git", "-C", repo, "diff", "--stat", f"{target_branch}...{source_branch}"]
        stat_res = subprocess.run(stat_cmd, capture_output=True, text=True, check=False)

        return {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "can_merge": diff_res.returncode == 0,
            "diff_stat": stat_res.stdout.strip(),
            "diff_content": diff_res.stdout[:50000],  # bounded preview
        }
