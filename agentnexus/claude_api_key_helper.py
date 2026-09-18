"""Cross-platform static credential delivery for Claude Code.

Claude Code executes ``settings.apiKeyHelper`` through the platform shell.
POSIX ``printf`` therefore fails under Windows ``cmd.exe``. Static credentials
use a dedicated inherited environment variable plus a Python helper command:
the command is portable, emits no trailing newline, and contains no credential
material that could appear in argv/error logs.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

CLAUDE_API_KEY_HELPER_TOKEN_ENV = "AGENTNEXUS_CLAUDE_API_KEY_HELPER_TOKEN"


def claude_api_key_helper_command() -> str:
    """Return a platform-shell command that prints the dedicated token env.

    The environment lookup is intentionally strict: a missing token makes the
    helper fail rather than printing an empty string and causing an opaque 401.
    No secret is embedded in the returned command.
    """
    # ``-m`` avoids an embedded quoted ``python -c`` program. cmd.exe applies
    # another quoting pass to its /c payload and otherwise hands Python a
    # literal opening quote (``SyntaxError: unterminated string literal``).
    argv = [sys.executable, "-m", "agentnexus.claude_api_key_helper"]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def main() -> None:
    """Print the helper token exactly, failing when the env is absent."""
    sys.stdout.write(os.environ[CLAUDE_API_KEY_HELPER_TOKEN_ENV])


if __name__ == "__main__":
    main()
