"""Workspace Lease and Git Coordination models and manager (WS-001, WS-002)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from omnigent.coordination.types import WorkspaceMergeOperation

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
    """Manages concurrent read/write leases on local repositories and worktrees.

    When a :class:`~omnigent.coordination.store.CoordinationStore` is injected,
    leases are persisted in the shared control-plane database, so a server or
    host restart keeps the fencing token instead of forgetting active writers.
    Without a store the manager keeps the legacy in-process behavior.
    """

    def __init__(self, store: Any = None) -> None:
        self._leases: dict[str, WorkspaceLease] = {}
        self._fencing_counters: dict[str, int] = {}
        self._lock = threading.Lock()
        self._store = store

    def _load_existing(self, norm_path: str) -> WorkspaceLease | None:
        if self._store is not None:
            loaded = self._store.load_active_lease(norm_path)
            if loaded is not None:
                self._leases[norm_path] = loaded
                self._fencing_counters[norm_path] = max(
                    self._fencing_counters.get(norm_path, 0), loaded.fencing_token
                )
        return self._leases.get(norm_path)

    def _persist(self, lease: WorkspaceLease, release: bool = False) -> None:
        if self._store is None:
            return
        if release:
            self._store.expire_lease(lease.workspace_path, lease.holder_session_id)
        else:
            self._store.save_lease(lease)

    def acquire(
        self,
        workspace_path: str | Path,
        holder_session_id: str,
        mode: LeaseMode = "write",
        duration_s: float = 600.0,
    ) -> WorkspaceLease:
        norm_path = os.path.normpath(str(workspace_path))
        with self._lock:
            existing = self._load_existing(norm_path)
            now = time.time()

            # Clean expired lease
            if existing and existing.is_expired:
                self._persist(existing, release=True)
                del self._leases[norm_path]
                existing = None

            if existing:
                # Same session renewing
                if existing.holder_session_id == holder_session_id:
                    existing.expires_at = now + duration_s
                    self._persist(existing)
                    return existing
                if mode == "write" or existing.mode == "write":
                    raise RuntimeError(f"Workspace locked by {existing.holder_session_id}")

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
            self._persist(lease)
            return lease

    def release(self, workspace_path: str | Path, holder_session_id: str) -> bool:
        norm_path = os.path.normpath(str(workspace_path))
        with self._lock:
            existing = self._load_existing(norm_path)
            if existing and existing.holder_session_id == holder_session_id:
                self._persist(existing, release=True)
                self._leases.pop(norm_path, None)
                return True
            return False

    def get_lease(self, workspace_path: str | Path) -> WorkspaceLease | None:
        norm_path = os.path.normpath(str(workspace_path))
        with self._lock:
            lease = self._load_existing(norm_path)
            if lease and lease.is_expired:
                self._persist(lease, release=True)
                self._leases.pop(norm_path, None)
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
            cmd = [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "-B",
                branch_name,
                str(worktree_dir),
                base_branch,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                # Fallback to existing branch or simple worktree add
                cmd_fb = [
                    "git",
                    "-C",
                    str(repo),
                    "worktree",
                    "add",
                    str(worktree_dir),
                    branch_name,
                ]
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

    @staticmethod
    def _git(repo: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run git against a repository without raising on non-zero exits."""
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def _branch_head(self, repo: str | Path, branch: str) -> str:
        res = self._git(repo, "rev-parse", "--verify", branch)
        if res.returncode != 0:
            raise RuntimeError(f"branch {branch!r} not found: {res.stderr.strip()[:200]}")
        return res.stdout.strip()

    def _dirty_hash(self, repo: str | Path) -> str:
        res = self._git(repo, "status", "--porcelain=v1")
        return hashlib.sha256(res.stdout.encode("utf-8")).hexdigest()

    def prepare_merge_preview(
        self,
        store: Any,
        *,
        root_session_id: str,
        holder_session_id: str,
        repo_path: str | Path,
        source_branch: str,
        target_branch: str = "main",
    ) -> WorkspaceMergeOperation:
        """Capture branch heads/dirty state and persist a user-confirmable merge preview."""
        repo = str(repo_path)
        lease = self.lease_manager.get_lease(repo)
        if (
            lease is None
            or lease.holder_session_id != holder_session_id
            or lease.mode != "write"
        ):
            raise RuntimeError(
                "active write lease required before creating a merge preview"
            )

        source_head = self._branch_head(repo, source_branch)
        target_head = self._branch_head(repo, target_branch)
        dirty_hash = self._dirty_hash(repo)

        diff_cmd = ["git", "-C", repo, "diff", f"{target_branch}...{source_branch}"]
        diff_res = subprocess.run(diff_cmd, capture_output=True, text=True, check=False)
        stat_cmd = ["git", "-C", repo, "diff", "--stat", f"{target_branch}...{source_branch}"]
        stat_res = subprocess.run(stat_cmd, capture_output=True, text=True, check=False)

        preview: dict[str, object] = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "can_merge": diff_res.returncode == 0,
            "diff_stat": stat_res.stdout.strip(),
            "diff_content": diff_res.stdout[:50000],  # bounded preview
        }
        operation = WorkspaceMergeOperation(
            root_session_id=root_session_id,
            holder_session_id=holder_session_id,
            repo_path=repo,
            source_branch=source_branch,
            target_branch=target_branch,
            expected_source_head=source_head,
            expected_target_head=target_head,
            dirty_hash=dirty_hash,
            fencing_token=lease.fencing_token,
            preview=preview,
        )
        store.create_merge_operation(operation)
        return operation

    def execute_merge(
        self,
        store: Any,
        *,
        operation_id: str,
        fencing_token: int,
    ) -> WorkspaceMergeOperation:
        """Execute a previewed merge only if the lease, heads and worktree still match."""
        operation = store.get_merge_operation(operation_id)
        if operation is None:
            raise RuntimeError(f"merge operation {operation_id!r} not found")
        if operation.status != "preview":
            raise RuntimeError(
                f"merge operation {operation_id!r} already has status {operation.status}"
            )

        lease = self.lease_manager.get_lease(operation.repo_path)
        if (
            lease is None
            or lease.holder_session_id != operation.holder_session_id
            or lease.mode != "write"
        ):
            raise RuntimeError("active write lease required to execute a merge")
        if lease.fencing_token != fencing_token:
            raise RuntimeError(
                f"fencing token mismatch: expected {lease.fencing_token}, got {fencing_token}"
            )

        current_branch = self._git(operation.repo_path, "branch", "--show-current").stdout.strip()
        if current_branch != operation.target_branch:
            raise RuntimeError(
                f"target branch {operation.target_branch!r} is not checked out "
                f"(current: {current_branch or 'detached'})"
            )

        source_head = self._branch_head(operation.repo_path, operation.source_branch)
        target_head = self._branch_head(operation.repo_path, operation.target_branch)
        if operation.expected_source_head and source_head != operation.expected_source_head:
            raise RuntimeError("source branch advanced since preview; create a new preview")
        if operation.expected_target_head and target_head != operation.expected_target_head:
            raise RuntimeError("target branch advanced since preview; create a new preview")
        if (
            operation.dirty_hash is not None
            and self._dirty_hash(operation.repo_path) != operation.dirty_hash
        ):
            raise RuntimeError("worktree changed since preview; create a new preview")

        store.update_merge_operation(operation.operation_id, status="executing")
        merge_res = self._git(
            operation.repo_path,
            "merge",
            "--no-ff",
            operation.source_branch,
            "-m",
            f"AgentNexus: merge {operation.source_branch} into {operation.target_branch}",
        )
        if merge_res.returncode != 0:
            self._git(operation.repo_path, "merge", "--abort")
            status = "conflict" if "CONFLICT" in merge_res.stderr else "failed"
            store.update_merge_operation(
                operation.operation_id,
                status=status,
                result={"error": merge_res.stderr.strip()[:2000]},
            )
            return store.get_merge_operation(operation.operation_id) or operation

        head = self._branch_head(operation.repo_path, "HEAD")
        store.update_merge_operation(
            operation.operation_id,
            status="merged",
            result={
                "source_branch": operation.source_branch,
                "target_branch": operation.target_branch,
                "expected_source_head": operation.expected_source_head,
                "expected_target_head": operation.expected_target_head,
                "merge_head": head,
            },
        )
        return store.get_merge_operation(operation.operation_id) or operation
