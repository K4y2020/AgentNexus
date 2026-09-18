"""Tests for coordination workspace ownership helpers."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentnexus.server.routes._coordination_workspace import (
    CoordinationWorkspaceError,
    canonical_workspace_path,
    is_path_within,
    managed_workspace_boundaries,
)


class TestCanonicalWorkspacePath:
    """Fail-closed input normalization for workspace paths."""

    def test_empty_rejected(self) -> None:
        with pytest.raises(CoordinationWorkspaceError):
            canonical_workspace_path("")

    def test_relative_rejected(self) -> None:
        with pytest.raises(CoordinationWorkspaceError):
            canonical_workspace_path("projects/agent")

    def test_tilde_rejected(self) -> None:
        with pytest.raises(CoordinationWorkspaceError):
            canonical_workspace_path("~/projects/agent")

    def test_absolute_normalized(self, tmp_path: Path) -> None:
        raw = str(tmp_path / "sub" / ".." / "project")
        assert canonical_workspace_path(raw) == os.path.normcase(
            os.path.realpath(str(tmp_path / "project"))
        )


class TestIsPathWithin:
    """Canonical containment checks against managed boundaries."""

    def test_same_path(self, tmp_path: Path) -> None:
        boundary = str(tmp_path / "repo")
        assert is_path_within(boundary, boundary)

    def test_child_path(self, tmp_path: Path) -> None:
        assert is_path_within(str(tmp_path / "repo" / "src"), str(tmp_path / "repo"))

    def test_prefix_collision(self, tmp_path: Path) -> None:
        assert not is_path_within(str(tmp_path / "repo-extra"), str(tmp_path / "repo"))

    def test_outside_rejected(self, tmp_path: Path) -> None:
        assert not is_path_within(str(tmp_path / "other"), str(tmp_path / "repo"))

    def test_filesystem_root_is_not_a_managed_boundary(self, tmp_path: Path) -> None:
        if os.name == "nt":
            root = os.path.splitdrive(str(tmp_path))[0] + os.sep
        else:
            root = os.sep
        assert not is_path_within(str(tmp_path / "x"), root)


class TestManagedWorkspaceBoundaries:
    """Boundary selection favors the holder, then falls back to the root."""

    def test_holder_then_root_fallback(self, tmp_path: Path) -> None:
        root_ws = str(tmp_path / "repo")
        holder_ws = str(tmp_path / "repo-worktrees" / "feat")
        root = SimpleNamespace(host_id=None, workspace=root_ws)
        holder = SimpleNamespace(host_id="host_a", workspace=holder_ws)
        host_id, boundaries = managed_workspace_boundaries(root, holder)
        assert host_id == "host_a"
        assert canonical_workspace_path(holder_ws) in boundaries
        assert canonical_workspace_path(root_ws) in boundaries

    def test_root_only_when_holder_has_no_workspace(self, tmp_path: Path) -> None:
        root = SimpleNamespace(host_id="host_a", workspace=str(tmp_path / "repo"))
        holder = SimpleNamespace(host_id=None, workspace=None)
        host_id, boundaries = managed_workspace_boundaries(root, holder)
        assert host_id == "host_a"
        assert len(boundaries) == 1

    def test_malformed_records_are_skipped(self) -> None:
        root = SimpleNamespace(host_id=None, workspace="relative/workspace")
        holder = SimpleNamespace(host_id="host_a", workspace=None)
        host_id, boundaries = managed_workspace_boundaries(root, holder)
        assert host_id == "host_a"
        assert boundaries == []
