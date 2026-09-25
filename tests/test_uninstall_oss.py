from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from agentnexus.install_ledger import sha256_text

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "uninstall_oss.sh"


def _run_uninstall(
    home: Path, *args: str, path: str | None = None, env_updates: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AGENTNEXUS_DATA_DIR"] = str(home / ".agentnexus")
    env["PATH"] = path or env.get("PATH", "")
    if env_updates:
        env.update(env_updates)
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )


def _fake_uv(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    uv_log = tmp_path / "uv.log"
    uv = fake_bin / "uv"
    uv.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {uv_log}\nexit 0\n")
    uv.chmod(0o755)
    return fake_bin, uv_log


def _path_without_zstd(tmp_path: Path) -> str:
    fake_bin = tmp_path / "no-zstd-bin"
    fake_bin.mkdir()
    for command in (
        "awk",
        "basename",
        "cat",
        "date",
        "dirname",
        "du",
        "find",
        "grep",
        "gzip",
        "mktemp",
        "mkdir",
        "ps",
        "rm",
        "sed",
        "sh",
        "sleep",
        "tar",
        "uname",
    ):
        target = shutil.which(command)
        if target is not None:
            (fake_bin / command).symlink_to(target)
    return str(fake_bin)


def test_uninstall_script_removes_profile_block_and_runs_wheel_last(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    profile = home / ".zshrc"
    profile.write_text(
        "keep\n"
        "# >>> AgentNexus installer >>>\n"
        'export PATH="/fake/bin:$PATH"\n'
        "# <<< AgentNexus installer <<<\n"
        "keep2\n"
    )
    fake_bin, uv_log = _fake_uv(tmp_path)

    result = _run_uninstall(
        home, "--yes", "--json", path=f"{fake_bin}:{os.environ.get('PATH', '')}"
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["done"] >= 2
    assert "AgentNexus installer" not in profile.read_text()
    assert profile.read_text() == "keep\nkeep2\n"
    assert uv_log.read_text().strip() == "tool uninstall omnigent"
    assert list(home.glob(".zshrc.agentnexus.bak.*"))


def test_uninstall_script_bare_command_is_dry_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    profile = home / ".zshrc"
    profile.write_text(
        "keep\n"
        "# >>> AgentNexus installer >>>\n"
        'export PATH="/fake/bin:$PATH"\n'
        "# <<< AgentNexus installer <<<\n"
        "keep2\n"
    )
    fake_bin, uv_log = _fake_uv(tmp_path)

    result = _run_uninstall(home, path=f"{fake_bin}:{os.environ.get('PATH', '')}")

    assert result.returncode == 0, result.stderr
    assert "reported: profile_block" in result.stdout
    assert "reported: wheel" in result.stdout
    assert "Preview only" in result.stdout
    assert profile.read_text().startswith("keep\n# >>> AgentNexus installer >>>")
    assert not uv_log.exists()


def test_uninstall_script_purge_backs_up_state_and_keeps_workspace_without_gate(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    workspace = home / "agentnexus"
    state.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (state / "config.yaml").write_text("x: y\n")
    (state / "installation_id").write_text("install-123\n")
    (workspace / "project.txt").write_text("work\n")

    result = _run_uninstall(home, "state", "--purge", "--yes", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not state.exists()
    assert workspace.exists()
    assert payload["backups"]
    assert all(Path(backup).parent != state for backup in payload["backups"])
    assert any(action.get("gate") == "--purge-workspace" for action in payload["actions"])


def test_uninstall_script_purge_without_target_also_removes_cli(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    state.mkdir(parents=True)
    (state / "installation_id").write_text("install-123\n")
    profile = home / ".zshrc"
    profile.write_text(
        "keep\n"
        "# >>> AgentNexus installer >>>\n"
        'export PATH="/fake/bin:$PATH"\n'
        "# <<< AgentNexus installer <<<\n"
        "keep2\n"
    )
    fake_bin, uv_log = _fake_uv(tmp_path)

    result = _run_uninstall(
        home, "--purge", "--yes", "--json", path=f"{fake_bin}:{os.environ.get('PATH', '')}"
    )

    assert result.returncode == 0, result.stderr
    assert not state.exists()
    assert "AgentNexus installer" not in profile.read_text()
    assert uv_log.read_text().strip() == "tool uninstall omnigent"


def test_uninstall_script_purge_uses_unique_backup_paths_for_multiple_trees(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    workspace = home / "agentnexus"
    linux_desktop_dirs = (
        home / ".config" / "AgentNexus",
        home / ".cache" / "AgentNexus",
        home / ".local" / "state" / "AgentNexus",
    )
    mac_desktop_dirs = (
        home / "Library" / "Application Support" / "AgentNexus",
        home / "Library" / "Caches" / "AgentNexus",
        home / "Library" / "Logs" / "AgentNexus",
    )
    for directory in (state, workspace, *linux_desktop_dirs, *mac_desktop_dirs):
        directory.mkdir(parents=True)
        (directory / "data.txt").write_text("data\n")
    (state / "installation_id").write_text("install-123\n")

    result = _run_uninstall(
        home,
        "all",
        "--purge",
        "--purge-workspace",
        "--yes",
        "--json",
        path=_path_without_zstd(tmp_path),
        env_updates={
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
        },
    )

    assert result.returncode == 0, result.stderr
    backups = json.loads(result.stdout)["backups"]
    assert len(backups) == 5
    assert len(backups) == len(set(backups))
    assert all(Path(backup).exists() for backup in backups)


def test_uninstall_script_refuses_tampered_profile_and_skips_wheel(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    original_block = (
        "# >>> AgentNexus installer >>>\n"
        'export PATH="/fake/bin:$PATH"\n'
        "# <<< AgentNexus installer <<<\n"
    )
    profile = home / ".zshrc"
    profile.write_text(original_block.replace("/fake/bin", "/tampered/bin"))
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\t".join(
            ["profile_block", str(profile), sha256_text(original_block), "recorded", "certain"]
        )
        + "\n"
    )
    fake_bin, uv_log = _fake_uv(tmp_path)
    env = os.environ.copy()
    env["AGENTNEXUS_UNINSTALL_LEDGER_MANIFEST"] = str(manifest)
    env["AGENTNEXUS_UNINSTALL_LEDGER_SOURCE"] = "backfill"
    env["HOME"] = str(home)
    env["AGENTNEXUS_DATA_DIR"] = str(home / ".agentnexus")
    env["PATH"] = f"{fake_bin}:{os.environ.get('PATH', '')}"

    result = subprocess.run(
        ["sh", str(SCRIPT), "--yes", "--json"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert any(action["gate"] == "--force" for action in payload["actions"])
    assert "tampered" in profile.read_text()
    assert not uv_log.exists()


def test_uninstall_script_external_config_requires_gate_then_removes_json_key(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "harness.json"
    config.write_text('{"mcp_servers": {"agentnexus": {"url": "x"}, "other": {}}}\n')
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\t".join(
            [
                "external_config",
                str(config),
                "mcp_servers.agentnexus",
                "json",
                "",
                "observed",
                "certain",
            ]
        )
        + "\n"
    )
    env = os.environ.copy()
    env["AGENTNEXUS_UNINSTALL_LEDGER_MANIFEST"] = str(manifest)
    env["AGENTNEXUS_UNINSTALL_LEDGER_SOURCE"] = "backfill"
    env["HOME"] = str(home)
    env["AGENTNEXUS_DATA_DIR"] = str(home / ".agentnexus")

    skipped = subprocess.run(
        ["sh", str(SCRIPT), "--yes", "--json"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    assert skipped.returncode == 0
    assert "agentnexus" in config.read_text()
    assert any(
        action["gate"] == "--modify-external-config"
        for action in json.loads(skipped.stdout)["actions"]
    )

    removed = subprocess.run(
        ["sh", str(SCRIPT), "--yes", "--json", "--modify-external-config"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert removed.returncode == 0, removed.stderr
    payload = json.loads(config.read_text())
    assert "agentnexus" not in payload["mcp_servers"]
    assert "other" in payload["mcp_servers"]


def test_uninstall_script_toml_config_and_launch_agent_reporting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.agentnexus]\ncommand = "agentnexus"\n\n'
        '[mcp_servers.other]\ncommand = "other"\n'
    )
    launch_agent = tmp_path / "ai.agentnexus.plist"
    launch_agent.write_text("plist\n")
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\t".join(
            [
                "external_config",
                str(config),
                "mcp_servers.agentnexus",
                "toml",
                "",
                "observed",
                "certain",
            ]
        )
        + "\n"
        + "\t".join(
            ["launch_agent", "launchd", str(launch_agent), "ai.agentnexus", "observed", "high"]
        )
        + "\n"
    )
    env = os.environ.copy()
    env["AGENTNEXUS_UNINSTALL_LEDGER_MANIFEST"] = str(manifest)
    env["HOME"] = str(home)
    env["AGENTNEXUS_DATA_DIR"] = str(home / ".agentnexus")

    result = subprocess.run(
        ["sh", str(SCRIPT), "--dry-run", "--json", "--modify-external-config"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    actions = json.loads(result.stdout)["actions"]
    assert any(action["artifact"] == "launch_agent" for action in actions)
    assert any("would remove" in action["detail"] for action in actions)

    removed = subprocess.run(
        ["sh", str(SCRIPT), "--yes", "--json", "--modify-external-config"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert removed.returncode == 0, removed.stderr
    assert "mcp_servers.agentnexus" not in config.read_text()
    assert "mcp_servers.other" in config.read_text()


def test_uninstall_script_unloads_launch_agent_before_stopping_host_pid(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    state.mkdir(parents=True)
    launch_agent = tmp_path / "ai.agentnexus.plist"
    launch_agent.write_text("plist\n")
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\t".join(
            ["launch_agent", "launchd", str(launch_agent), "ai.agentnexus", "observed", "high"]
        )
        + "\n"
    )
    proc = subprocess.Popen(["sleep", "60"])
    (state / "host.pid").write_text(f"{proc.pid}\nlocal\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "launchctl").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "launchctl").chmod(0o755)

    try:
        result = _run_uninstall(
            home,
            "--yes",
            "--json",
            path=f"{fake_bin}:{os.environ.get('PATH', '')}",
            env_updates={
                "AGENTNEXUS_UNINSTALL_LEDGER_MANIFEST": str(manifest),
                "AGENTNEXUS_UNINSTALL_LEDGER_SOURCE": "installer",
            },
        )

        assert result.returncode == 0, result.stderr
        proc.wait(timeout=5)
        assert not launch_agent.exists()
        actions = json.loads(result.stdout)["actions"]
        launch_index = next(
            index for index, action in enumerate(actions) if action["artifact"] == "launch_agent"
        )
        process_index = next(
            index for index, action in enumerate(actions) if action["artifact"] == "process"
        )
        assert launch_index < process_index
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_uninstall_script_external_json_preserves_key_order(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "harness.json"
    config.write_text('{"z": 1, "mcp_servers": {"other": {}, "agentnexus": {}}, "a": 2}\n')
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\t".join(
            [
                "external_config",
                str(config),
                "mcp_servers.agentnexus",
                "json",
                "",
                "observed",
                "certain",
            ]
        )
        + "\n"
    )

    result = _run_uninstall(
        home,
        "--yes",
        "--json",
        "--modify-external-config",
        env_updates={
            "AGENTNEXUS_UNINSTALL_LEDGER_MANIFEST": str(manifest),
            "AGENTNEXUS_UNINSTALL_LEDGER_SOURCE": "backfill",
        },
    )

    assert result.returncode == 0, result.stderr
    text = config.read_text()
    assert text.index('"z"') < text.index('"mcp_servers"') < text.index('"a"')
    assert "agentnexus" not in text


def test_uninstall_script_toml_removes_nested_subtables(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.agentnexus]\ncommand = "agentnexus"\n\n'
        '[mcp_servers.agentnexus.env]\nFOO = "bar"\n\n'
        '[mcp_servers.other]\ncommand = "other"\n'
    )
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "\t".join(
            [
                "external_config",
                str(config),
                "mcp_servers.agentnexus",
                "toml",
                "",
                "observed",
                "certain",
            ]
        )
        + "\n"
    )

    result = _run_uninstall(
        home,
        "--yes",
        "--json",
        "--modify-external-config",
        env_updates={
            "AGENTNEXUS_UNINSTALL_LEDGER_MANIFEST": str(manifest),
            "AGENTNEXUS_UNINSTALL_LEDGER_SOURCE": "backfill",
        },
    )

    assert result.returncode == 0, result.stderr
    text = config.read_text()
    assert "mcp_servers.agentnexus" not in text
    assert "mcp_servers.other" in text


def test_uninstall_script_refuses_without_install_signal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    result = _run_uninstall(home, "--dry-run", "--json", path="/bin:/usr/bin")

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 3
    assert any(action["artifact"] == "anchor" for action in payload["actions"])


def test_uninstall_script_removes_fish_profile_blocks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fish_conf = home / ".config" / "fish" / "config.fish"
    fish_confd = home / ".config" / "fish" / "conf.d" / "agentnexus.fish"
    fish_conf.parent.mkdir(parents=True)
    fish_confd.parent.mkdir(parents=True)
    block = (
        "# >>> AgentNexus installer >>>\n"
        "set -gx PATH /fake/bin $PATH\n"
        "# <<< AgentNexus installer <<<\n"
    )
    fish_conf.write_text(f"keep\n{block}keep2\n")
    fish_confd.write_text(f"before\n{block}after\n")

    result = _run_uninstall(home, "--yes", "--json", path="/bin:/usr/bin")

    assert result.returncode == 0, result.stderr
    assert fish_conf.read_text() == "keep\nkeep2\n"
    assert fish_confd.read_text() == "before\nafter\n"


def test_uninstall_script_purge_no_backup_removes_state_without_archive(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    state.mkdir(parents=True)
    (state / "installation_id").write_text("install-123\n")

    result = _run_uninstall(
        home, "state", "--purge", "--no-backup", "--yes", "--json", path="/bin:/usr/bin"
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not state.exists()
    assert payload["backups"] == []


def test_uninstall_script_purge_uses_gzip_when_zstd_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    state.mkdir(parents=True)
    (state / "installation_id").write_text("install-123\n")
    (state / "config.yaml").write_text("x: y\n")

    result = _run_uninstall(
        home, "state", "--purge", "--yes", "--json", path=_path_without_zstd(tmp_path)
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["backups"]
    assert payload["backups"][0].endswith(".tar.gz")


def test_uninstall_script_purge_namespaces_xdg_state_backups(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state_home = tmp_path / "xdg-state"
    state = home / ".agentnexus"
    state.mkdir(parents=True)
    (state / "installation_id").write_text("install-123\n")
    (state / "config.yaml").write_text("x: y\n")

    result = _run_uninstall(
        home,
        "state",
        "--purge",
        "--yes",
        "--json",
        path="/bin:/usr/bin",
        env_updates={"XDG_STATE_HOME": str(state_home)},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["backups"]
    assert Path(payload["backups"][0]).parent == state_home / "agentnexus-backups"


def test_uninstall_script_keeps_state_when_zstd_backup_tar_fails(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    state.mkdir(parents=True)
    (state / "installation_id").write_text("install-123\n")
    (state / "config.yaml").write_text("x: y\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "zstd").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "tar").write_text("#!/bin/sh\nexit 1\n")
    (fake_bin / "zstd").chmod(0o755)
    (fake_bin / "tar").chmod(0o755)

    result = _run_uninstall(
        home,
        "state",
        "--purge",
        "--yes",
        "--json",
        path=f"{fake_bin}:{os.environ.get('PATH', '')}",
    )

    assert result.returncode == 1
    assert state.exists()
    payload = json.loads(result.stdout)
    assert payload["backups"] == []


def test_uninstall_script_stops_live_pid_from_state_run_dir(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    run_dir = state / "run"
    run_dir.mkdir(parents=True)
    (state / "installation_id").write_text("install-123\n")
    proc = subprocess.Popen(["sleep", "60"])
    try:
        (run_dir / "daemon.pid").write_text(f"{proc.pid}\n")
        result = _run_uninstall(home, "state", "--dry-run", "--json")
        assert result.returncode == 0
        assert proc.poll() is None

        result = _run_uninstall(home, "state", "--yes", "--json")
        assert result.returncode == 0, result.stderr
        proc.wait(timeout=5)
        assert any(
            action["artifact"] == "process" and action["status"] == "done"
            for action in json.loads(result.stdout)["actions"]
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_uninstall_script_rerun_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".agentnexus"
    state.mkdir(parents=True)
    (state / "installation_id").write_text("install-123\n")
    profile = home / ".zshrc"
    profile.write_text(
        "keep\n"
        "# >>> AgentNexus installer >>>\n"
        'export PATH="/fake/bin:$PATH"\n'
        "# <<< AgentNexus installer <<<\n"
    )
    fake_bin, _ = _fake_uv(tmp_path)
    path = f"{fake_bin}:{os.environ.get('PATH', '')}"

    first = _run_uninstall(home, "--yes", "--json", path=path)
    second = _run_uninstall(home, "--yes", "--json", path=path)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "AgentNexus installer" not in profile.read_text()
