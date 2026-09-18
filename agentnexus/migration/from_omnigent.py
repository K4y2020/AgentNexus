"""Configuration migration from Omnigent to AgentNexus.

Automatically migrates:
- ~/.omnigent/ → ~/.agentnexus/
- Environment variables (with backward compatibility)
- User configuration files

Runs automatically on first launch of AgentNexus CLI.
"""

import os
import shutil
import warnings
from pathlib import Path
from typing import Optional


def get_old_config_dir() -> Path:
    """Get the old Omnigent config directory."""
    return Path.home() / ".omnigent"


def get_new_config_dir() -> Path:
    """Get the new AgentNexus config directory."""
    return Path.home() / ".agentnexus"


def should_migrate() -> bool:
    """Check if migration is needed."""
    old_dir = get_old_config_dir()
    new_dir = get_new_config_dir()

    # Migrate if old dir exists and new dir doesn't
    return old_dir.exists() and not new_dir.exists()


def migrate_config_directory() -> bool:
    """Migrate ~/.omnigent/ to ~/.agentnexus/.

    Returns:
        True if migration was performed, False otherwise.
    """
    if not should_migrate():
        return False

    old_dir = get_old_config_dir()
    new_dir = get_new_config_dir()

    try:
        print(f"🔄 Migrating configuration from {old_dir} to {new_dir}...")

        # Copy entire directory tree
        shutil.copytree(old_dir, new_dir, symlinks=True, dirs_exist_ok=False)

        print(f"✅ Configuration migrated successfully!")
        print(f"   Old config preserved at: {old_dir}")
        print(f"   New config location: {new_dir}")
        print()
        print("   You can safely delete the old directory after verifying everything works:")
        print(f"   rm -rf {old_dir}")

        return True

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        print(f"   Please manually copy {old_dir} to {new_dir}")
        return False


def get_env_var(key: str, compat: bool = True) -> Optional[str]:
    """Get environment variable with backward compatibility.

    Args:
        key: Variable name without prefix (e.g., "CONFIG_HOME")
        compat: If True, fall back to OMNIGENT_* variables

    Returns:
        Value from AGENTNEXUS_* or OMNIGENT_* (with deprecation warning)
    """
    new_var = f"AGENTNEXUS_{key}"
    old_var = f"OMNIGENT_{key}"

    # Try new variable first
    value = os.getenv(new_var)
    if value is not None:
        return value

    # Fall back to old variable with warning
    if compat:
        value = os.getenv(old_var)
        if value is not None:
            warnings.warn(
                f"{old_var} is deprecated and will be removed in AgentNexus 2.0. "
                f"Please use {new_var} instead.",
                DeprecationWarning,
                stacklevel=2
            )
            return value

    return None


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

    return get_config_home() / "data"


# Environment variable compatibility layer
class EnvCompat:
    """Backward-compatible environment variable access."""

    @staticmethod
    def get(key: str, default: Optional[str] = None) -> Optional[str]:
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
def get_server_url() -> Optional[str]:
    """Get AGENTNEXUS_SERVER_URL (or deprecated OMNIGENT_SERVER_URL)."""
    return get_env_var("SERVER_URL")


def get_auth_enabled() -> bool:
    """Get AGENTNEXUS_AUTH_ENABLED (or deprecated OMNIGENT_AUTH_ENABLED)."""
    return EnvCompat.getbool("AUTH_ENABLED", default=False)


def get_log_level() -> str:
    """Get AGENTNEXUS_LOG_LEVEL (or deprecated OMNIGENT_LOG_LEVEL)."""
    return EnvCompat.get("LOG_LEVEL", default="INFO")


def get_model_default() -> Optional[str]:
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
