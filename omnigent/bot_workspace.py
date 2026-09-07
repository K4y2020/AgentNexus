"""Session-scoped directories for Bot tasks without a project checkout."""

import re
from pathlib import Path

WORKSPACE_LAYOUT_LABEL = "omnigent.workspace_layout"
WORKSPACE_INSTRUCTIONS = (
    "This session has its own task workspace. Keep original inputs in inputs/, "
    "editable sources and reusable task scripts in work/, final deliverables in outputs/, "
    "and disposable screenshots, diagnostics and browser profiles in temp/. "
    "Do not accumulate task files in the Bot Home or shared scratch directory. "
    "Delegate with this task's explicit workspace and these directory rules; "
    "do not use the latest session's directory. Preserve existing relative references. "
    "For separate tasks within this chat use named subdirectories in work/ and outputs/. "
    "Do not automatically delete or move existing files, browser profiles or user inputs."
)


def ensure_bot_task_workspace(home_path: str, session_id: str) -> str:
    """Create an isolated, stable task directory without touching legacy files."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise ValueError("Invalid workspace session identifier")
    home = Path(home_path).resolve()
    task = home / "topics" / session_id
    paths = [task, *(task / name for name in ("inputs", "work", "outputs", "temp"))]
    for path in paths:
        if not path.resolve().is_relative_to(home):
            raise ValueError("Bot task workspace escapes its home")
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    return str(task)
