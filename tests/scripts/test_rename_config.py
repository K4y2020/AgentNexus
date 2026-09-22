"""Cross-package config compatibility without importing the server."""

from pathlib import Path

import pytest
import yaml
from agentnexus_ui_sdk.terminal import _config


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for prefix in ("AGENTNEXUS_", "OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"):
        for suffix in ("CONFIG_HOME", "DATA_DIR"):
            monkeypatch.delenv(prefix + suffix, raising=False)
    return tmp_path


@pytest.mark.parametrize("prefix", ["OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"])
def test_sdk_config_environment_precedence(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    legacy = isolated_home / "old-config"
    monkeypatch.setenv(prefix + "CONFIG_HOME", str(legacy))
    assert _config.user_config_path() == legacy / "config.yaml"
    monkeypatch.setenv("AGENTNEXUS_CONFIG_HOME", "")
    assert _config.user_config_path() == isolated_home / ".agentnexus" / "config.yaml"


def test_sdk_reads_legacy_config_and_preserves_siblings_when_saving(isolated_home: Path) -> None:
    legacy = isolated_home / ".omnigent" / "config.yaml"
    legacy.parent.mkdir()
    original = "default_agent: kept\ntui:\n  theme: light\n"
    legacy.write_text(original)
    assert _config.load_user_config().theme == "light"
    saved = _config.save_user_config(_config.UserConfig(theme="dark"))
    assert saved == isolated_home / ".agentnexus" / "config.yaml"
    assert yaml.safe_load(saved.read_text()) == {"default_agent": "kept", "tui": {"theme": "dark"}}
    assert legacy.read_text() == original


def test_sdk_explicit_empty_config_does_not_read_legacy(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = isolated_home / ".omnigent" / "config.yaml"
    legacy.parent.mkdir()
    legacy.write_text("tui:\n  theme: light\n")
    monkeypatch.setenv("AGENTNEXUS_CONFIG_HOME", "")
    assert _config.load_user_config() == _config.DEFAULT_USER_CONFIG


def test_sdk_existing_empty_config_wins_over_legacy(isolated_home: Path) -> None:
    legacy = isolated_home / ".omnigent" / "config.yaml"
    legacy.parent.mkdir()
    legacy.write_text("tui:\n  theme: light\n")
    canonical = isolated_home / ".agentnexus" / "config.yaml"
    canonical.parent.mkdir()
    canonical.touch()
    assert _config.load_user_config() == _config.DEFAULT_USER_CONFIG
