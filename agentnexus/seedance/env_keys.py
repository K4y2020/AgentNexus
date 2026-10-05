"""Read a Cine service key from the environment or a ``.env`` file.

The film-analysis pipeline runs without agentnexus and reads TYPESAFE_API_KEY
through its own ``jev_transport.find_api_key``; both search the same files in the
same order, so the runner and a Topic resolve the same key.
"""

from __future__ import annotations

import os
from pathlib import Path


def env_files() -> list[Path]:
    """The ``.env`` files searched for a key, in order."""
    config_home = os.environ.get("AGENTNEXUS_CONFIG_HOME")
    home = Path(config_home) if config_home else Path.home() / ".agentnexus"
    return [Path.cwd() / ".env", home / ".env", Path.home() / ".env"]


def read_key(name: str) -> str:
    """``name`` from the environment, else from the first ``.env`` file that sets it."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    prefix = f"{name}="
    for path in env_files():
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            line = line.strip()
            if line.startswith(prefix):
                value = line[len(prefix) :].strip().strip('"').strip("'")
                if value:
                    return value
    return ""
