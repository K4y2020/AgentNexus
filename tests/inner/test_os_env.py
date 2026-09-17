"""Unit tests for :mod:`omnigent.inner.os_env` helper-env construction."""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import sys
import time
import tracemalloc
from pathlib import Path

import psutil
import pytest

from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec
from omnigent.inner.os_env import (
    _child_shell_env,
    _project_root,
    _read_impl,
    _shell_impl,
    build_helper_env,
    create_os_environment,
)
from omnigent.inner.sandbox import SandboxPolicy
from omnigent.runner.identity import (
    OMNIGENT_SESSION_ENV_VALUE,
    OMNIGENT_SESSION_ENV_VAR,
    RUNNER_TUNNEL_BINDING_TOKEN_ENV_VAR,
)


def _inactive_policy() -> SandboxPolicy:
    """A ``sandbox.type: none`` policy (user opted out of sandboxing).

    :returns: An inactive :class:`SandboxPolicy` whose ``build_helper_env``
        branch mirrors the parent environment.
    """
    return SandboxPolicy(
        backend_type="none",
        active=False,
        read_roots=None,
        write_roots=[],
        write_files=[],
        allow_network=True,
    )


def _active_policy() -> SandboxPolicy:
    """An active policy that drives ``build_helper_env``'s allowlist branch.

    ``build_helper_env`` only consults ``active`` and ``env_passthrough``;
    the ``backend_type`` is never activated here, so ``"none"`` is fine.

    :returns: An active :class:`SandboxPolicy`.
    """
    return SandboxPolicy(
        backend_type="none",
        active=True,
        read_roots=None,
        write_roots=[],
        write_files=[],
        allow_network=True,
    )


def test_build_helper_env_inactive_strips_binding_token() -> None:
    """``sandbox.type: none`` mirrors parent env MINUS the binding token.

    Opting out of sandboxing grants the agent broad
    file/network access, but it must NOT additionally leak the runner's
    control-plane auth secret. Asserts ``PATH`` survives (the opt-out
    still mirrors the parent env) while the token is dropped.

    :returns: None.
    """
    parent = {
        "PATH": "/usr/bin",
        RUNNER_TUNNEL_BINDING_TOKEN_ENV_VAR: "bug-binding-token-secret",
    }

    env = build_helper_env(parent, _inactive_policy())

    assert RUNNER_TUNNEL_BINDING_TOKEN_ENV_VAR not in env
    assert "bug-binding-token-secret" not in env.values()
    assert "/usr/bin" in env["PATH"].split(os.pathsep)


def test_build_helper_env_active_drops_binding_token() -> None:
    """The active allowlist branch never admits the binding token.

    The deny-by-default allowlist excludes the token's name, so even if
    it is present in the parent env it does not reach the helper.

    :returns: None.
    """
    parent = {
        "PATH": "/usr/bin",
        RUNNER_TUNNEL_BINDING_TOKEN_ENV_VAR: "bug-binding-token-secret",
    }

    env = build_helper_env(parent, _active_policy())

    assert RUNNER_TUNNEL_BINDING_TOKEN_ENV_VAR not in env
    assert "bug-binding-token-secret" not in env.values()
    assert "/usr/bin" in env["PATH"].split(os.pathsep)  # PATH is in the default allowlist


def test_build_helper_env_active_passes_omnigent_session_marker() -> None:
    """The ``OMNIGENT`` session marker survives the active allowlist.

    The marker (set once on the runner process) must reach an agent's
    sandboxed shell so code running there can detect it is inside an
    Omnigent session, the way ``CLAUDE_CODE`` / ``CODEX`` are visible in
    their own agents' shells.

    :returns: None.
    """
    parent = {
        "PATH": "/usr/bin",
        OMNIGENT_SESSION_ENV_VAR: OMNIGENT_SESSION_ENV_VALUE,
    }

    env = build_helper_env(parent, _active_policy())

    assert env[OMNIGENT_SESSION_ENV_VAR] == OMNIGENT_SESSION_ENV_VALUE


def test_build_helper_env_appends_tool_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper env gains user tool dirs appended to PATH.

    A Windows sub-agent shell can miss tools (uv/ffmpeg/uv-managed CPython)
    that exist on the interactive PATH when the server inherits a trimmed
    PATH. ``build_helper_env`` appends only the directories reported by
    ``_windows_tool_paths``, keeps the caller's own ordering first and never
    duplicates an entry already on PATH.

    :returns: None.
    """
    parent = {"PATH": os.pathsep.join(["/usr/bin", "/opt/tools"])}
    monkeypatch.setattr(
        "omnigent.inner.os_env._windows_tool_paths",
        lambda: ["C:\\tools\\uv", "C:\\tools\\ffmpeg", "/opt/tools"],
    )
    env = build_helper_env(parent, _inactive_policy())
    entries = env["PATH"].split(os.pathsep)
    assert "/usr/bin" in entries
    assert "/opt/tools" in entries
    assert "C:\\tools\\uv" in entries
    assert "C:\\tools\\ffmpeg" in entries
    assert entries.count("/opt/tools") == 1


# ---------------------------------------------------------------------------
# _shell_impl — timeout result shape
# ---------------------------------------------------------------------------


def test_shell_impl_timeout_includes_exit_code(tmp_path: Path) -> None:
    """Timed-out shell commands still return the documented result fields."""
    shell_path = shutil.which("bash") or shutil.which("sh")
    assert shell_path is not None

    result = _shell_impl(
        command="sleep 2",
        timeout=1,
        shell_path=shell_path,
        cwd=tmp_path,
    )

    assert result["stdout"] == ""
    assert result["stderr"] == ""
    assert result["exit_code"] is None
    assert result["timed_out"] is True
    assert result["error"] == "Command timed out after 1 seconds"


def test_shell_impl_does_not_wait_for_detached_child_pipe(tmp_path: Path) -> None:
    """A background descendant cannot keep a completed shell call open."""
    pid_file = tmp_path / "child.pid"
    child_code = "import time; time.sleep(5)"
    parent_code = (
        "import subprocess, sys; "
        f"p=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"open({str(pid_file)!r}, 'w').write(str(p.pid)); "
        "print('launched', flush=True)"
    )

    started = time.monotonic()
    try:
        result = _shell_impl(
            command=parent_code,
            timeout=1,
            shell_path=sys.executable,
            cwd=tmp_path,
        )
        elapsed = time.monotonic() - started

        assert elapsed < 2.0
        assert result["timed_out"] is False
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "launched"
        assert pid_file.exists()
        assert psutil.pid_exists(int(pid_file.read_text()))
    finally:
        if pid_file.exists():
            try:
                child = psutil.Process(int(pid_file.read_text()))
                child.kill()
                child.wait(timeout=2)
            except psutil.NoSuchProcess:
                pass


def test_shell_impl_prefers_utf8_output(tmp_path: Path) -> None:
    result = _shell_impl(
        command="import sys; sys.stdout.buffer.write('中文诊断 ✗'.encode('utf-8'))",
        timeout=5,
        shell_path=sys.executable,
        cwd=tmp_path,
    )
    assert result["stdout"] == "中文诊断 ✗"


# ---------------------------------------------------------------------------
# _read_impl — binary file handling
# ---------------------------------------------------------------------------

_BINARY = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff"


def test_read_impl_binary_descriptor_for_agent(tmp_path: Path) -> None:
    """With no byte cap (agent ``sys_os_read`` path) binary is not inlined.

    The base64 payload would be useless to the model and could saturate the
    context window, so only a descriptor is returned.

    :returns: None.
    """
    f = tmp_path / "logo.png"
    f.write_bytes(_BINARY)

    result = _read_impl(f, offset=1, limit=2_000)

    assert result["encoding"] == "base64"
    assert result["content"] == ""
    assert result["total_bytes"] == len(_BINARY)
    # Not truncated — the payload was deliberately omitted, not cut short.
    assert result["truncated"] is False
    assert "note" in result


def test_read_impl_binary_inlined_within_cap(tmp_path: Path) -> None:
    """A byte cap larger than the file inlines the whole payload, untruncated.

    :returns: None.
    """
    f = tmp_path / "logo.png"
    f.write_bytes(_BINARY)

    result = _read_impl(f, offset=1, limit=2_000, max_binary_bytes=10 * 1024 * 1024)

    assert result["encoding"] == "base64"
    assert base64.b64decode(result["content"]) == _BINARY
    assert result["total_bytes"] == len(_BINARY)
    assert result["truncated"] is False


def test_read_impl_binary_truncated_at_cap(tmp_path: Path) -> None:
    """A byte cap smaller than the file truncates and flags it.

    :returns: None.
    """
    f = tmp_path / "logo.png"
    f.write_bytes(_BINARY)

    result = _read_impl(f, offset=1, limit=2_000, max_binary_bytes=4)

    assert base64.b64decode(result["content"]) == _BINARY[:4]
    assert result["returned_bytes"] == 4
    assert result["total_bytes"] == len(_BINARY)
    assert result["truncated"] is True


def _make_large_binary(path: Path, size: int) -> None:
    """Write a sparse file with a binary prefix and a logical size of *size*.

    The 8 KB binary prefix forces the prefix-sniff to classify it binary; the
    ``truncate`` extends the (sparse) file to *size* without writing the bytes,
    so the test stays cheap while exercising a large logical file.

    :returns: None.
    """
    with path.open("wb") as fh:
        fh.write(b"\xff\xfe\x00\x01" * 2_048)  # 8 KB of non-UTF-8 bytes
        fh.truncate(size)


def test_read_impl_binary_descriptor_does_not_read_whole_file(tmp_path: Path) -> None:
    """The descriptor path is O(1): it stats the size, never reading content.

    Regression guard for inlining the whole file (``path.read_bytes()``) just
    to compute ``total_bytes`` — which would OOM on large workspace blobs.

    :returns: None.
    """
    size = 256 * 1024 * 1024  # 256 MB logical, only ~8 KB on disk
    f = tmp_path / "big.bin"
    _make_large_binary(f, size)

    tracemalloc.start()
    try:
        result = _read_impl(f, offset=1, limit=2_000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result["total_bytes"] == size
    assert result["content"] == ""
    # A full read would have allocated ~256 MB; bounded reads stay tiny.
    assert peak < 10 * 1024 * 1024


def test_read_impl_binary_cap_reads_only_the_cap(tmp_path: Path) -> None:
    """The byte-capped path reads at most ``max_binary_bytes``, not the file.

    :returns: None.
    """
    size = 256 * 1024 * 1024
    f = tmp_path / "big.bin"
    _make_large_binary(f, size)

    tracemalloc.start()
    try:
        result = _read_impl(f, offset=1, limit=2_000, max_binary_bytes=16)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result["returned_bytes"] == 16
    assert result["total_bytes"] == size
    assert result["truncated"] is True
    assert peak < 10 * 1024 * 1024


def test_read_impl_multibyte_char_straddling_sniff_boundary_is_text(tmp_path: Path) -> None:
    """A multi-byte char split across the 8 KB sniff boundary stays text.

    The incremental decoder must treat the truncated trailing sequence as
    *incomplete*, not invalid — otherwise valid UTF-8 would be misread as
    binary purely because of where the prefix happened to be cut.

    :returns: None.
    """
    # 8 KB sniff window cuts the 3-byte '€' (0xE2 0x82 0xAC) at byte 8191.
    text = "a" * 8_190 + "€" + "tail\n"
    f = tmp_path / "wide.txt"
    f.write_text(text, encoding="utf-8")

    result = _read_impl(f, offset=1, limit=2_000)

    assert result["encoding"] == "utf-8"
    assert result["content"] == text


def test_read_impl_nul_byte_file_classified_binary(tmp_path: Path) -> None:
    """A NUL byte marks a file binary even though ``\\x00`` is valid UTF-8.

    UTF-16/NUL-laden files decode cleanly as UTF-8, so without an explicit NUL
    check they'd be misread as text and line-windowed into garbage.

    :returns: None.
    """
    # UTF-16-LE-style ASCII: every byte is valid UTF-8, but the interleaved
    # NULs make this binary.
    f = tmp_path / "utf16.bin"
    f.write_bytes(b"H\x00e\x00l\x00l\x00o\x00")

    result = _read_impl(f, offset=1, limit=2_000)

    assert result["encoding"] == "base64"
    assert result["total_bytes"] == 10


# ---------------------------------------------------------------------------
# _child_shell_env — omnigent's own package root must not leak onto the
# PYTHONPATH of agent shell commands (it would shadow the project's packages).
# ---------------------------------------------------------------------------


def test_child_shell_env_strips_project_root_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """omnigent's project root is removed; a project entry is preserved.

    The helper prepends its project root to ``PYTHONPATH`` so it can import
    omnigent at startup. Commands the agent runs must not inherit that entry,
    or omnigent's ``site-packages`` shadows the project venv's own packages.

    :returns: None.
    """
    project_entry = "/opt/venvs/proj/lib/python3.13/site-packages"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(_project_root()), project_entry]))

    env = _child_shell_env()

    assert env["PYTHONPATH"] == project_entry


def test_child_shell_env_drops_var_when_only_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the sole entry is omnigent's root, ``PYTHONPATH`` is unset.

    Leaving an empty ``PYTHONPATH`` would put the shell command's cwd on
    ``sys.path``; dropping the var entirely avoids that surprise.

    :returns: None.
    """
    monkeypatch.setenv("PYTHONPATH", str(_project_root()))

    env = _child_shell_env()

    assert "PYTHONPATH" not in env


def test_child_shell_env_noop_without_pythonpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``PYTHONPATH`` in the parent env means nothing to strip.

    :returns: None.
    """
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = _child_shell_env()

    assert "PYTHONPATH" not in env
    assert env["PATH"] == "/usr/bin"


# ---------------------------------------------------------------------------
# End-to-end: the real helper must not leak omnigent's package root into a
# sys_os_shell command's PYTHONPATH. Guards the wiring in _shell_impl, not
# just _child_shell_env in isolation.
# ---------------------------------------------------------------------------


def test_shell_command_does_not_see_omnigent_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shell command's ``PYTHONPATH`` drops omnigent's root, keeps the rest.

    Spawns a real ``caller_process`` helper (``sandbox: none`` so it runs on
    every platform) with omnigent's root pre-seeded on ``PYTHONPATH`` — the
    same shape the helper spawn produces — and asserts the agent's command
    sees the sibling project entry but not omnigent's, so project subprocesses
    resolve their own packages.

    :returns: None.
    """
    project_entry = "/opt/venvs/proj/site-packages"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(_project_root()), project_entry]))

    os_env = create_os_environment(
        OSEnvSpec(type="caller_process", sandbox=OSEnvSandboxSpec(type="none"))
    )
    assert os_env is not None
    try:
        command = (
            'Write-Output ("PP=" + $env:PYTHONPATH)' if os.name == "nt" else "echo PP=$PYTHONPATH"
        )
        result = asyncio.run(os_env.shell(command))
    finally:
        os_env.close()

    out = result.get("stdout", "")
    assert project_entry in out
    assert str(_project_root()) not in out


@pytest.mark.skipif(os.name != "nt", reason="Windows config-file transport regression")
def test_inactive_helper_reads_file_on_windows(tmp_path: Path) -> None:
    """An unsandboxed Windows helper still creates its config transport dir."""
    target = tmp_path / "hello.txt"
    target.write_text("WINDOWS_HELPER_OK", encoding="utf-8")
    os_env = create_os_environment(
        OSEnvSpec(
            type="caller_process",
            cwd=str(tmp_path),
            sandbox=OSEnvSandboxSpec(type="none"),
        )
    )
    assert os_env is not None
    try:
        result = asyncio.run(os_env.read("hello.txt"))
    finally:
        os_env.close()

    assert result.get("content") == "WINDOWS_HELPER_OK"
