"""Configuration migration from Omnigent to AgentNexus.

Automatically migrates:
- ~/.omnigent/ → ~/.agentnexus/
- Environment variables (with backward compatibility)
- User configuration files

Runs automatically on first launch of AgentNexus CLI.
"""

from pathlib import Path

from agentnexus._env_compat import get_env_var as _get_env_var


def get_old_config_dir() -> Path:
    """Return the first historical state directory; supported until 2.0."""
    from agentnexus.cli import _LEGACY_STATE_DIRS

    return next((path for path in _LEGACY_STATE_DIRS if path.exists()), _LEGACY_STATE_DIRS[0])


def get_new_config_dir() -> Path:
    """Get the new AgentNexus config directory."""
    return Path.home() / ".agentnexus"


def should_migrate() -> bool:
    """Check for old state without an explicit config/data override."""
    if get_env_var("CONFIG_HOME") or get_env_var("DATA_DIR"):
        return False
    old_dir = get_old_config_dir()
    new_dir = get_new_config_dir()

    # Migrate if old dir exists and new dir doesn't
    return old_dir.exists() and not new_dir.exists()


def migrate_config_directory() -> bool:
    """Use the CLI's guarded, one-time migration (legacy inputs removed in 2.0)."""
    from agentnexus.cli import _STATE_DIR, _migrate_legacy_state_dir

    existed = _STATE_DIR.exists()
    _migrate_legacy_state_dir()
    return not existed and _STATE_DIR.exists()


def get_env_var(key: str, compat: bool = True) -> str | None:
    """Use the same prefix precedence as package startup; legacy support ends in 2.0."""
    return _get_env_var(key, compat=compat)


def get_config_home() -> Path:
    """Get the config directory, respecting environment variables."""
    # Check new variable
    config_home = get_env_var("CONFIG_HOME")
    if config_home:
        return Path(config_home)

    # Use new default
    return get_new_config_dir()


def get_data_dir() -> Path:
    """Get the data directory, respecting environment variables."""
    data_dir = get_env_var("DATA_DIR")
    if data_dir:
        return Path(data_dir)

    # CONFIG_HOME only moves config.yaml; runtime state has its own override.
    return get_new_config_dir()


# Environment variable compatibility layer
class EnvCompat:
    """Backward-compatible environment variable access."""

    @staticmethod
    def get(key: str, default: str | None = None) -> str | None:
        """Get env var with OMNIGENT_* → AGENTNEXUS_* compat."""
        value = get_env_var(key, compat=True)
        return value if value is not None else default

    @staticmethod
    def getbool(key: str, default: bool = False) -> bool:
        """Get boolean env var with compat."""
        value = get_env_var(key, compat=True)
        if value is None:
            return default
        return value.lower() in ("1", "true", "yes", "on")

    @staticmethod
    def getint(key: str, default: int = 0) -> int:
        """Get integer env var with compat."""
        value = get_env_var(key, compat=True)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default


# Common environment variables
def get_server_url() -> str | None:
    """Get AGENTNEXUS_SERVER_URL (or deprecated OMNIGENT_SERVER_URL)."""
    return get_env_var("SERVER_URL")


def get_auth_enabled() -> bool:
    """Get AGENTNEXUS_AUTH_ENABLED (or deprecated OMNIGENT_AUTH_ENABLED)."""
    return EnvCompat.getbool("AUTH_ENABLED", default=False)


def get_log_level() -> str:
    """Get AGENTNEXUS_LOG_LEVEL (or deprecated OMNIGENT_LOG_LEVEL)."""
    return EnvCompat.get("LOG_LEVEL", default="INFO")


def get_model_default() -> str | None:
    """Get AGENTNEXUS_MODEL_DEFAULT (or deprecated OMNIGENT_MODEL_DEFAULT)."""
    return get_env_var("MODEL_DEFAULT")


if __name__ == "__main__":
    # Run migration if needed
    if migrate_config_directory():
        print("\n✨ Migration complete! AgentNexus is ready to use.")
    else:
        old_dir = get_old_config_dir()
        new_dir = get_new_config_dir()
        if old_dir.exists() and new_dir.exists():
            print(f"ℹ️  Both {old_dir} and {new_dir} exist.")
            print(f"   Using {new_dir}")
        elif new_dir.exists():
            print(f"✅ Already using {new_dir}")
        else:
            print(f"ℹ️  No existing configuration found. Will create {new_dir} on first use.")
