"""Backward-compatibility shim for the env-var prefix renames -> ``AGENTNEXUS_*``.

Current code reads ``AGENTNEXUS_`` names. Until version 2.0, the legacy
``OMNIGENT_``, ``OMNIGENTS_`` and ``OMNIAGENTS_`` prefixes remain supported.
To keep existing deployments, CI configs, and shell profiles working,
this shim mirrors every legacy variable onto its ``AGENTNEXUS_`` equivalent at
process startup -- but only when the new name is unset, so an explicitly-set
``AGENTNEXUS_`` value always wins.

The mirror is installed once, as early as possible, from
``agentnexus/__init__.py`` so it runs before any submodule reads the
environment. Out-of-package entry points that read env *before* importing the
``agentnexus`` package (the Docker / Databricks deploy entrypoints) call
:func:`mirror_legacy_env` directly.
"""

from __future__ import annotations

import os
import warnings

# The current prefix, and every legacy prefix that maps onto it. Ordered
# newest-first so that when more than one legacy prefix is set for the same
# variable, the newer one wins (``setdefault`` keeps the first mirrored value).
# Legacy reads are deprecated and will be removed in 2.0.
_NEW_PREFIX = "AGENTNEXUS_"
_LEGACY_PREFIXES = ("OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_")

# Module-level guard so repeated imports/calls don't rescan the environment.
_mirrored = False


def get_env_var(key: str, *, compat: bool = True) -> str | None:
    """Read a suffix such as DATA_DIR; an explicit new value, even empty, wins."""
    name = _NEW_PREFIX + key
    if name in os.environ:
        return os.environ[name]
    if compat:
        for prefix in _LEGACY_PREFIXES:
            legacy_name = prefix + key
            if legacy_name in os.environ:
                warnings.warn(
                    f"{legacy_name} is deprecated and will be removed in AgentNexus 2.0. "
                    f"Use {name} instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return os.environ[legacy_name]
    return None


def mirror_legacy_env() -> None:
    """
    Mirror legacy prefixes onto ``AGENTNEXUS_*`` until their removal in 2.0.

    For every environment variable whose name starts with one of the legacy
    prefixes in :data:`_LEGACY_PREFIXES`, set the corresponding ``AGENTNEXUS_``
    variable if (and only if) it is not already present -- so an explicitly-set
    new-name variable always takes precedence over a legacy one, and a newer
    legacy prefix takes precedence over an older one. Idempotent and cheap:
    calls after the first are no-ops.

    Example: with ``OMNIAGENTS_SKIP_WEB_UI=1`` in the environment and no
    ``AGENTNEXUS_SKIP_WEB_UI`` set, this leaves ``AGENTNEXUS_SKIP_WEB_UI=1``.

    :returns: ``None``. Mutates :data:`os.environ` in place.
    """
    global _mirrored
    if _mirrored:
        return
    for legacy_prefix in _LEGACY_PREFIXES:
        for name, value in list(os.environ.items()):
            if name.startswith(legacy_prefix):
                new_name = _NEW_PREFIX + name[len(legacy_prefix) :]
                if new_name not in os.environ:
                    warnings.warn(
                        f"{name} is deprecated and will be removed in AgentNexus 2.0. "
                        f"Use {new_name} instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    os.environ[new_name] = value
    _mirrored = True
