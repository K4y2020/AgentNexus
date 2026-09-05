"""Cine pipeline — project create/lock/atomic-write/resume (C0)."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from .schemas import ProjectRecord, RunState, RunStatus, SCHEMA_VERSION

# optional filelock — graceful fallback if somehow absent
try:
    from filelock import FileLock, Timeout as FileLockTimeout
    _FILELOCK_OK = True
except ImportError:
    _FILELOCK_OK = False


# ---------------------------------------------------------------------------
# Helpers & Path Security (W3)
# ---------------------------------------------------------------------------

_ILLEGAL_ID_CHARS = set('<>:"/\\|?*\0')


def validate_identifier(val: str, name: str = "identifier") -> str:
    """Validate identifier against path traversal and illegal filesystem characters."""
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if ".." in val:
        raise ValueError(f"Path traversal detected in {name}: {val!r}")
    if "/" in val or "\\" in val:
        raise ValueError(f"Path separator detected in {name}: {val!r}")
    if any(c in _ILLEGAL_ID_CHARS or ord(c) < 32 for c in val):
        raise ValueError(f"Illegal characters detected in {name}: {val!r}")
    return val

def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically: temp file on same FS, then os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use the same directory for the temp file so rename is atomic on Windows
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

def project_root(projects_dir: Path, project_id: str) -> Path:
    validate_identifier(project_id, "project_id")
    return projects_dir / project_id


def revision_root(projects_dir: Path, project_id: str, revision_id: str) -> Path:
    validate_identifier(project_id, "project_id")
    validate_identifier(revision_id, "revision_id")
    return project_root(projects_dir, project_id) / "revisions" / revision_id


def run_root(projects_dir: Path, project_id: str, run_id: str) -> Path:
    validate_identifier(project_id, "project_id")
    validate_identifier(run_id, "run_id")
    return project_root(projects_dir, project_id) / "runs" / run_id


# ---------------------------------------------------------------------------
# Project lifecycle
# ---------------------------------------------------------------------------

class ProjectLock:
    """Context manager that holds the filelock for a project."""

    def __init__(self, lock_path: Path, timeout: float = 0.0):
        self._lock_path = lock_path
        self._timeout = timeout
        self._lock = None

    def __enter__(self) -> "ProjectLock":
        if not _FILELOCK_OK:
            raise RuntimeError("filelock is not installed; cannot acquire project lock")
        self._lock = FileLock(str(self._lock_path))
        try:
            self._lock.acquire(timeout=self._timeout)
        except FileLockTimeout:
            raise RuntimeError(
                f"Could not acquire project lock at {self._lock_path} "
                f"(timeout={self._timeout}s). Another writer may be active."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._lock is not None:
            self._lock.release()


def create_project(
    projects_dir: Path,
    display_name: str,
    *,
    project_id: Optional[str] = None,
) -> ProjectRecord:
    """
    Create a new project directory and project.json.
    Raises if the directory already exists (prevents accidental overwrites).
    """
    if not display_name or not display_name.strip():
        raise ValueError("display_name must not be empty")
    if project_id is not None:
        validate_identifier(project_id, "project_id")
    pid = project_id or uuid.uuid4().hex
    root = project_root(projects_dir, pid)
    if root.exists():
        raise FileExistsError(
            f"Project directory already exists: {root}. "
            "Use resume_project() to continue an existing project."
        )
    root.mkdir(parents=True, exist_ok=False)
    record = ProjectRecord(project_id=pid, display_name=display_name)
    _atomic_write(root / "project.json", record.model_dump())
    return record


def resume_project(projects_dir: Path, project_id: str) -> ProjectRecord:
    """Load an existing project by ID."""
    path = project_root(projects_dir, project_id) / "project.json"
    if not path.exists():
        raise FileNotFoundError(f"No project found at {path}")
    data = _read_json(path)
    return ProjectRecord.model_validate(data)


def update_project(projects_dir: Path, record: ProjectRecord) -> None:
    """Atomically overwrite project.json with an updated record."""
    root = project_root(projects_dir, record.project_id)
    _atomic_write(root / "project.json", record.model_dump())


def save_run(projects_dir: Path, state: RunState) -> None:
    """Persist run state atomically."""
    path = run_root(projects_dir, state.project_id, state.run_id) / "run.json"
    _atomic_write(path, state.model_dump())


def load_run(projects_dir: Path, project_id: str, run_id: str) -> RunState:
    path = run_root(projects_dir, project_id, run_id) / "run.json"
    return RunState.model_validate(_read_json(path))


def create_revision(
    projects_dir: Path,
    project_id: str,
) -> str:
    """Create a new immutable revision directory and return its ID."""
    rev_id = uuid.uuid4().hex
    rev_dir = revision_root(projects_dir, project_id, rev_id)
    rev_dir.mkdir(parents=True, exist_ok=False)
    (rev_dir / "evidence").mkdir(exist_ok=True)
    (rev_dir / "report").mkdir(exist_ok=True)
    return rev_id


def lock_path(projects_dir: Path, project_id: str) -> Path:
    return project_root(projects_dir, project_id) / ".writer.lock"
