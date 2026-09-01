"""Fail-closed workspace ownership checks for coordination workspace operations.

The Control Plane only leases or merges paths that are already recorded as
managed workspaces of sessions in the coordination tree. Session workspaces are
canonicalized at create time via host.stat realpath, so this module compares
requested paths against those boundaries and rejects anything else.
"""

from __future__ import annotations

import os
from typing import Any


class CoordinationWorkspaceError(Exception):
    """Raised when a workspace path fails the managed-workspace checks."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def canonical_workspace_path(path: str) -> str:
    """Return a normalized absolute path for containment comparisons.

    Tilde and relative inputs fail closed: the server never expands ``~`` and
    never lets the coordination layer address a path by its relative spelling.
    Symlinks are resolved (best effort on the local machine) so an escape
    through a link resolves outside the managed boundary and is rejected.
    """
    if not isinstance(path, str) or not path.strip():
        raise CoordinationWorkspaceError("workspace path must be an absolute path")
    if not os.path.isabs(path):
        raise CoordinationWorkspaceError(
            "workspace path must be an absolute path (relative paths are not accepted)"
        )
    return os.path.normcase(os.path.realpath(path))


def is_path_within(candidate: str, boundary: str) -> bool:
    """Return True when ``candidate`` equals ``boundary`` or is nested under it.

    Both values are canonicalized before comparison so separator spelling,
    ``..`` segments and symlinks cannot smuggle a path outside the boundary.
    Filesystem roots (``/`` or ``C:\\``) are intentionally rejected as managed
    boundaries: they would permit every path on the machine.
    """
    candidate_norm = canonical_workspace_path(candidate)
    boundary_norm = canonical_workspace_path(boundary)
    if candidate_norm == boundary_norm:
        return True
    sep = os.sep
    root_boundary = os.path.dirname(boundary_norm) == boundary_norm
    if root_boundary:
        return False
    prefix = boundary_norm if boundary_norm.endswith(sep) else boundary_norm + sep
    return candidate_norm.startswith(prefix)


def managed_workspace_boundaries(root: Any, holder: Any) -> tuple[str | None, list[str]]:
    """Return (host_id, canonical boundaries) for a coordination tree.

    The holder's own worktree is the primary boundary; the root's source repo
    is the secondary boundary so a merge on the source repository is still
    allowed for sessions running in sibling worktrees. Malformed records are
    skipped so the caller fails closed instead of trusting them.
    """
    host_id = getattr(holder, "host_id", None) or getattr(root, "host_id", None)
    boundaries: list[str] = []
    for conv in (holder, root):
        workspace = getattr(conv, "workspace", None)
        if not isinstance(workspace, str) or not workspace.strip():
            continue
        try:
            canonical = canonical_workspace_path(workspace)
        except CoordinationWorkspaceError:
            continue
        if canonical not in boundaries:
            boundaries.append(canonical)
    return host_id, boundaries
