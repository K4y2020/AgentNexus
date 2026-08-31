"""Cross-platform Claude apiKeyHelper command tests."""

from __future__ import annotations

import os
import subprocess

from omnigent._platform import default_shell_argv
from omnigent.claude_api_key_helper import (
    CLAUDE_API_KEY_HELPER_TOKEN_ENV,
    claude_api_key_helper_command,
)


def test_static_claude_api_key_helper_outputs_exact_token_without_exposing_it() -> None:
    token = "sk-test-&|% special token"
    command = claude_api_key_helper_command()
    env = {**os.environ, CLAUDE_API_KEY_HELPER_TOKEN_ENV: token}

    result = subprocess.run(
        default_shell_argv(command),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == token
    assert result.stderr == ""
    assert token not in command


def test_static_claude_api_key_helper_fails_when_token_env_is_missing() -> None:
    command = claude_api_key_helper_command()
    env = dict(os.environ)
    env.pop(CLAUDE_API_KEY_HELPER_TOKEN_ENV, None)

    result = subprocess.run(
        default_shell_argv(command),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
