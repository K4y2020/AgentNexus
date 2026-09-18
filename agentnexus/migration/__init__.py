"""Migration package for AgentNexus.

Handles automatic migration from Omnigent to AgentNexus.
"""

from .from_omnigent import (
    EnvCompat,
    get_auth_enabled,
    get_config_home,
    get_data_dir,
    get_env_var,
    get_log_level,
    get_model_default,
    get_server_url,
    migrate_config_directory,
    should_migrate,
)

__all__ = [
    "EnvCompat",
    "get_auth_enabled",
    "get_config_home",
    "get_data_dir",
    "get_env_var",
    "get_log_level",
    "get_model_default",
    "get_server_url",
    "migrate_config_directory",
    "should_migrate",
]
