"""Legacy configuration remains readable through the AgentNexus 2.0 transition."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import agentnexus.cli as cli
from agentnexus import _env_compat
from agentnexus.migration.from_omnigent import get_env_var, migrate_config_directory


@pytest.mark.parametrize("prefix", ["OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"])
def test_package_startup_maps_each_legacy_prefix(prefix: str) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENTNEXUS_", "OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"))
    }
    env[prefix + "RENAME_PROBE"] = "legacy-value"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agentnexus, os; print(os.environ.get('AGENTNEXUS_RENAME_PROBE'))",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert result.stdout.strip() == "legacy-value"


@pytest.mark.parametrize("new_value", ["new-value", ""])
def test_explicit_new_value_wins_including_empty(monkeypatch, new_value: str) -> None:
    monkeypatch.setenv("AGENTNEXUS_RENAME_PROBE", new_value)
    for prefix in ("OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"):
        monkeypatch.setenv(prefix + "RENAME_PROBE", "old-value")
    monkeypatch.setattr(_env_compat, "_mirrored", False)
    _env_compat.mirror_legacy_env()
    assert os.environ["AGENTNEXUS_RENAME_PROBE"] == new_value
    assert get_env_var("RENAME_PROBE") == new_value


def test_legacy_precedence_matches_helper_and_startup(monkeypatch) -> None:
    monkeypatch.delenv("AGENTNEXUS_RENAME_PROBE", raising=False)
    for prefix in ("OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"):
        monkeypatch.setenv(prefix + "RENAME_PROBE", prefix)
    with pytest.warns(DeprecationWarning, match="2.0"):
        assert get_env_var("RENAME_PROBE") == "OMNIGENT_"
    assert get_env_var("RENAME_PROBE", compat=False) is None
    monkeypatch.setattr(_env_compat, "_mirrored", False)
    with pytest.warns(DeprecationWarning, match="2.0"):
        _env_compat.mirror_legacy_env()
    assert os.environ["AGENTNEXUS_RENAME_PROBE"] == "OMNIGENT_"


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(cli, "_STATE_DIR", tmp_path / ".agentnexus")
    monkeypatch.setattr(
        cli, "_LEGACY_STATE_DIRS", tuple(tmp_path / path.name for path in cli._LEGACY_STATE_DIRS)
    )
    monkeypatch.delenv("AGENTNEXUS_CONFIG_HOME", raising=False)
    monkeypatch.delenv("AGENTNEXUS_DATA_DIR", raising=False)
    return tmp_path


@pytest.mark.parametrize("legacy_name", [".omnigent", ".omnigents", ".omniagents"])
def test_historical_directory_is_migrated_once(state_home: Path, legacy_name: str) -> None:
    old = state_home / legacy_name
    old.mkdir()
    (old / "config.yaml").write_text("harness: codex\n", encoding="utf-8")
    assert migrate_config_directory() is True
    assert not old.exists()
    assert (cli._STATE_DIR / "config.yaml").read_text(encoding="utf-8") == "harness: codex\n"
    assert migrate_config_directory() is False


def test_new_state_is_never_overwritten(state_home: Path) -> None:
    old = state_home / ".omnigent"
    old.mkdir()
    (old / "config.yaml").write_text("harness: old\n", encoding="utf-8")
    cli._STATE_DIR.mkdir()
    current = cli._STATE_DIR / "config.yaml"
    current.write_text("harness: new\n", encoding="utf-8")
    assert migrate_config_directory() is False
    assert current.read_text(encoding="utf-8") == "harness: new\n"
    assert (old / "config.yaml").exists()


@pytest.mark.parametrize("override", ["AGENTNEXUS_CONFIG_HOME", "AGENTNEXUS_DATA_DIR"])
def test_explicit_state_override_skips_migration(state_home: Path, monkeypatch, override) -> None:
    old = state_home / ".omnigent"
    old.mkdir()
    monkeypatch.setenv(override, str(state_home / "isolated"))
    assert migrate_config_directory() is False
    assert old.exists()
    assert not cli._STATE_DIR.exists()


def test_running_legacy_host_prevents_migration(state_home: Path, monkeypatch) -> None:
    old = state_home / ".omnigent"
    old.mkdir()
    (old / "host.pid").write_text("12345\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: pid == 12345)
    assert migrate_config_directory() is False
    assert old.exists()
    assert not cli._STATE_DIR.exists()


def test_migration_failure_preserves_old_state(state_home: Path, monkeypatch) -> None:
    old = state_home / ".omnigent"
    old.mkdir()
    (old / "config.yaml").write_text("harness: codex\n", encoding="utf-8")

    def fail_move(*_args, **_kwargs):
        raise PermissionError("test: destination unavailable")

    monkeypatch.setattr(cli.shutil, "move", fail_move)
    assert migrate_config_directory() is False
    assert (old / "config.yaml").read_text(encoding="utf-8") == "harness: codex\n"
    assert not cli._STATE_DIR.exists()
